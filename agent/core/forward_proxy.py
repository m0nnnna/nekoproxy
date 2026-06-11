"""HTTP(S) forward proxy server.

Devices set their HTTP/HTTPS proxy to agent-ip:NEKO_AGENT_FORWARD_PROXY_PORT
and their outbound traffic flows through this agent. When NEKO_AGENT_UPSTREAM_PROXY
is set, connections exit from that proxy (e.g. a VPS SOCKS5) so the device's
traffic appears to come from the VPS IP.

Supports:
  - HTTP CONNECT tunneling (HTTPS and any TCP tunnel)
  - Plain HTTP proxy (GET/POST/etc to http:// targets)
  - Optional Proxy-Authorization (Basic auth)
"""

import asyncio
import base64
import logging
import socket
import time
import types
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# Hostname → (address-list, expiry) cache so burst media loads don't each pay
# a getaddrinfo round-trip for the same CDN hostname. Bounded so federation to
# thousands of unique remote hosts can't grow it without limit.
_dns_cache: dict = {}
_DNS_CACHE_MAX = 4096


async def _resolve(host: str) -> list:
    loop = asyncio.get_running_loop()
    now = loop.time()
    entry = _dns_cache.get(host)
    if entry and now < entry[1]:
        return entry[0]
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    addrs = [i[4][0] for i in infos if i[4][0]]
    if addrs:
        if len(_dns_cache) >= _DNS_CACHE_MAX:
            # Drop expired entries first; if still full, clear the lot. Cheap and
            # bounded — federation churn never lets the dict grow without limit.
            for k in [k for k, (_, exp) in _dns_cache.items() if exp <= now]:
                _dns_cache.pop(k, None)
            if len(_dns_cache) >= _DNS_CACHE_MAX:
                _dns_cache.clear()
        _dns_cache[host] = (addrs, now + 60.0)
    return addrs


_CONNECT_ESTABLISHED = b"HTTP/1.1 200 Connection established\r\n\r\n"
_BAD_GATEWAY = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n"
_BAD_REQUEST = b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n"
_UNAVAILABLE = (
    b"HTTP/1.1 503 Service Unavailable\r\n"
    b"Retry-After: 1\r\nContent-Length: 0\r\n\r\n"
)
_PROXY_AUTH_REQUIRED = (
    b"HTTP/1.1 407 Proxy Authentication Required\r\n"
    b"Proxy-Authenticate: Basic realm=\"NekoProxy\"\r\n"
    b"Content-Length: 0\r\n\r\n"
)

# Headers stripped before forwarding plain HTTP requests upstream
_HOP_BY_HOP = frozenset({
    "proxy-authorization", "proxy-connection", "proxy-authenticate",
    "connection", "keep-alive", "te", "trailers", "transfer-encoding", "upgrade",
})


async def _open_backend(host: str, port: int, upstream_proxy: Optional[str]):
    """Open a TCP connection to host:port, optionally via an upstream proxy."""
    if upstream_proxy:
        try:
            from python_socks.async_.asyncio import Proxy
            proxy = Proxy.from_url(upstream_proxy)
            sock = await asyncio.wait_for(
                proxy.connect(dest_host=host, dest_port=port), timeout=15
            )
            return await asyncio.open_connection(sock=sock, limit=256 * 1024)
        except ImportError:
            raise RuntimeError("python-socks required for upstream proxy; run: pip install python-socks[asyncio]")
    # Pre-resolve with cache, then connect directly to IP so the OS DNS thread
    # pool isn't hit for every parallel media request to the same CDN hostname.
    # happy_eyeballs_delay lets IPv4 race IPv6 after 250 ms so a broken/slow
    # IPv6 route on the agent doesn't stall media loads from dual-stack CDNs.
    addrs = await _resolve(host)
    target = addrs[0] if addrs else host
    return await asyncio.wait_for(
        asyncio.open_connection(target, port, limit=256 * 1024, happy_eyeballs_delay=0.25),
        timeout=15,
    )


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 idle_timeout: float = 0) -> int:
    """One-directional byte relay until EOF, error, or idle timeout.

    idle_timeout (seconds, 0 = none) reaps a connection that goes silent in this
    direction — that's how zombie tunnels get cleaned up without an absolute
    lifetime cap that would kill active long transfers. Returns bytes relayed.
    """
    total = 0
    try:
        while True:
            if idle_timeout > 0:
                data = await asyncio.wait_for(reader.read(65536), timeout=idle_timeout)
            else:
                data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
            total += len(data)
    except Exception:
        # Includes asyncio.TimeoutError from the idle guard above.
        pass
    return total


def _fwd_stat(client_ip: str, client_port: int, target: str, status: str,
              duration: float, bytes_sent: int, bytes_received: int):
    return types.SimpleNamespace(
        client_ip=client_ip,
        client_port=client_port,
        service_id=None,
        status=status,
        duration=duration,
        bytes_sent=bytes_sent,
        bytes_received=bytes_received,
        proxy_type="forward",
        target=target,
    )


class ForwardProxyServer:
    """Asyncio-based HTTP(S) forward proxy."""

    def __init__(
        self,
        listen_ip: str,
        port: int,
        upstream_proxy: Optional[str] = None,
        auth: Optional[str] = None,
        on_connection: Optional[Callable] = None,
        max_connections: int = 500,
        idle_timeout: int = 180,
        overflow_wait: int = 5,
        max_per_host: int = 32,
    ):
        self._listen_ip = listen_ip
        self._port = port
        self._upstream = upstream_proxy
        self._auth: Optional[tuple] = None
        if auth and ":" in auth:
            u, p = auth.split(":", 1)
            self._auth = (u.strip(), p.strip())
        self._on_connection = on_connection
        self._server: Optional[asyncio.Server] = None
        # Hard ceiling on concurrent connections so federation bursts can't
        # exhaust file descriptors / memory and take the agent down.
        self._max_connections = max(1, max_connections)
        self._idle_timeout = max(0, idle_timeout)
        self._overflow_wait = max(0, overflow_wait)
        self._max_per_host = max(0, max_per_host)
        self._sem = asyncio.Semaphore(self._max_connections)
        self._active = 0
        # Per-destination connection counts so one slow/dead remote can't hold a
        # large share of the global pool and starve healthy federation traffic.
        self._host_counts: dict = {}

    def _try_acquire_host(self, host: str) -> bool:
        if self._max_per_host <= 0:
            return True
        n = self._host_counts.get(host, 0)
        if n >= self._max_per_host:
            return False
        self._host_counts[host] = n + 1
        return True

    def _release_host(self, host: str):
        if self._max_per_host <= 0:
            return
        n = self._host_counts.get(host, 0) - 1
        if n <= 0:
            self._host_counts.pop(host, None)
        else:
            self._host_counts[host] = n

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle_client, self._listen_ip, self._port,
            reuse_address=True, backlog=256,
        )
        addr = self._server.sockets[0].getsockname()
        auth_note = " (auth required)" if self._auth else ""
        upstream_note = f" → upstream {self._upstream}" if self._upstream else " → direct"
        logger.info("Forward proxy listening on %s:%d%s%s [max %d conns, idle %ds]",
                    addr[0], addr[1], upstream_note, auth_note,
                    self._max_connections, self._idle_timeout)
        async with self._server:
            await self._server.serve_forever()

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info("Forward proxy stopped")

    def _check_auth(self, headers: dict) -> bool:
        if not self._auth:
            return True
        raw = headers.get("proxy-authorization", "")
        if not raw.lower().startswith("basic "):
            return False
        try:
            decoded = base64.b64decode(raw[6:]).decode("utf-8", errors="replace")
            u, _, p = decoded.partition(":")
            return (u, p) == self._auth
        except Exception:
            return False

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername", ("?", 0))
        # Bound concurrency: grab a slot or shed the connection fast with 503.
        # Waiting briefly absorbs short bursts; refusing past that keeps a
        # federation stampede from exhausting FDs and crashing the agent.
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=self._overflow_wait or 0.001)
        except asyncio.TimeoutError:
            logger.warning(
                "Forward proxy at capacity (%d active) — shedding connection from %s:%s",
                self._active, peer[0], peer[1],
            )
            try:
                writer.write(_UNAVAILABLE)
                await writer.drain()
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            return

        self._active += 1
        try:
            # No absolute lifetime cap here: request parsing has its own per-read
            # timeouts and the relay loops use an idle timeout, so long-lived but
            # active media/keepalive tunnels survive while dead ones get reaped.
            await self._process(reader, writer, peer)
        except Exception as e:
            logger.debug("Forward proxy error from %s:%s — %s", peer[0], peer[1], e)
        finally:
            self._active -= 1
            self._sem.release()
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _process(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, peer):
        # Read request line
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=15)
        except asyncio.TimeoutError:
            writer.write(_BAD_REQUEST)
            return
        if not line:
            return

        parts = line.decode("utf-8", errors="replace").strip().split()
        if len(parts) < 2:
            writer.write(_BAD_REQUEST)
            return
        method = parts[0].upper()
        target = parts[1]

        # Read headers
        raw_headers: list[bytes] = []
        headers: dict[str, str] = {}
        while True:
            try:
                hline = await asyncio.wait_for(reader.readline(), timeout=10)
            except asyncio.TimeoutError:
                writer.write(_BAD_REQUEST)
                return
            if hline in (b"\r\n", b"\n", b""):
                break
            raw_headers.append(hline)
            text = hline.decode("utf-8", errors="replace").strip()
            if ":" in text:
                k, _, v = text.partition(":")
                headers[k.strip().lower()] = v.strip()

        if not self._check_auth(headers):
            writer.write(_PROXY_AUTH_REQUIRED)
            await writer.drain()
            return

        if method == "CONNECT":
            await self._handle_connect(target, reader, writer, peer)
        else:
            await self._handle_http(method, target, headers, raw_headers, reader, writer, peer)

    async def _handle_connect(self, target: str, reader, writer, peer):
        """Tunnel raw TCP for HTTPS (and anything else using CONNECT)."""
        host, _, port_str = target.rpartition(":")
        port = int(port_str) if port_str.isdigit() else 443
        host = host.strip("[]")  # strip IPv6 brackets

        # Per-destination cap: don't let a slow/dead remote hog the pool.
        if not self._try_acquire_host(host):
            logger.warning("Per-host cap (%d) hit for %s — shedding CONNECT from %s:%s",
                           self._max_per_host, host, peer[0], peer[1])
            writer.write(_UNAVAILABLE)
            await writer.drain()
            if self._on_connection:
                self._on_connection(_fwd_stat(peer[0], peer[1], target, "rejected", 0, 0, 0))
            return

        logger.debug("CONNECT %s from %s:%s", target, peer[0], peer[1])
        start = time.monotonic()
        try:
            try:
                remote_reader, remote_writer = await _open_backend(host, port, self._upstream)
            except Exception as e:
                logger.warning("CONNECT to %s failed: %s", target, e)
                writer.write(_BAD_GATEWAY)
                await writer.drain()
                if self._on_connection:
                    self._on_connection(_fwd_stat(peer[0], peer[1], target, "failed",
                                                  time.monotonic() - start, 0, 0))
                return

            writer.write(_CONNECT_ESTABLISHED)
            await writer.drain()

            t1 = asyncio.create_task(_relay(reader, remote_writer, self._idle_timeout))
            t2 = asyncio.create_task(_relay(remote_reader, writer, self._idle_timeout))
            done, pending = await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            def _task_bytes(t: asyncio.Task) -> int:
                if t.done() and not t.cancelled() and t.exception() is None:
                    return t.result()
                return 0

            bytes_up = _task_bytes(t1)
            bytes_down = _task_bytes(t2)
            try:
                remote_writer.close()
                await remote_writer.wait_closed()
            except Exception:
                pass
            if self._on_connection:
                self._on_connection(_fwd_stat(peer[0], peer[1], target, "completed",
                                              time.monotonic() - start, bytes_up, bytes_down))
        finally:
            self._release_host(host)

    async def _handle_http(self, method: str, target: str, headers: dict, raw_headers: list, reader, writer, peer):
        """Forward a plain HTTP proxy request."""
        # Parse target URL
        if target.startswith("http://"):
            target = target[7:]
        slash = target.find("/")
        if slash == -1:
            host_part, path = target, "/"
        else:
            host_part, path = target[:slash], target[slash:]

        if ":" in host_part:
            host, _, port_str = host_part.rpartition(":")
            port = int(port_str) if port_str.isdigit() else 80
        else:
            host, port = host_part, 80

        content_length = int(headers.get("content-length", 0) or 0)

        # Per-destination cap: don't let a slow/dead remote hog the pool.
        if not self._try_acquire_host(host):
            logger.warning("Per-host cap (%d) hit for %s — shedding HTTP from %s:%s",
                           self._max_per_host, host, peer[0], peer[1])
            writer.write(_UNAVAILABLE)
            await writer.drain()
            if self._on_connection:
                self._on_connection(_fwd_stat(peer[0], peer[1], f"{host}:{port}", "rejected", 0, 0, 0))
            return

        logger.debug("HTTP %s %s:%d%s from %s:%s", method, host, port, path, peer[0], peer[1])
        start = time.monotonic()
        try:
            try:
                remote_reader, remote_writer = await _open_backend(host, port, self._upstream)
            except Exception as e:
                logger.warning("HTTP proxy to %s:%d failed: %s", host, port, e)
                writer.write(_BAD_GATEWAY)
                await writer.drain()
                if self._on_connection:
                    self._on_connection(_fwd_stat(peer[0], peer[1], f"{host}:{port}", "failed",
                                                  time.monotonic() - start, 0, 0))
                return

            # Rebuild request, stripping hop-by-hop and proxy headers
            rebuilt = f"{method} {path} HTTP/1.1\r\n"
            for raw in raw_headers:
                text = raw.decode("utf-8", errors="replace").strip()
                key = text.split(":")[0].strip().lower()
                if key not in _HOP_BY_HOP:
                    rebuilt += text + "\r\n"
            rebuilt += "Connection: close\r\n\r\n"

            head_bytes = rebuilt.encode()
            remote_writer.write(head_bytes)
            await remote_writer.drain()

            # Stream the request body in chunks rather than buffering the whole
            # Content-Length in memory — concurrent large media uploads would
            # otherwise spike RAM and OOM the agent.
            bytes_sent = len(head_bytes)
            remaining = content_length
            while remaining > 0:
                try:
                    chunk = await asyncio.wait_for(
                        reader.read(min(65536, remaining)), timeout=30
                    )
                except asyncio.TimeoutError:
                    break
                if not chunk:
                    break
                remote_writer.write(chunk)
                await remote_writer.drain()
                bytes_sent += len(chunk)
                remaining -= len(chunk)

            # Relay response back to client (idle-timeout reaps a stalled backend)
            resp_bytes = await _relay(remote_reader, writer, self._idle_timeout)
            try:
                remote_writer.close()
                await remote_writer.wait_closed()
            except Exception:
                pass
            if self._on_connection:
                self._on_connection(_fwd_stat(peer[0], peer[1], f"{host}:{port}", "completed",
                                              time.monotonic() - start, bytes_sent, resp_bytes))
        finally:
            self._release_host(host)
