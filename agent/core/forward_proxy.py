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
# a getaddrinfo round-trip for the same CDN hostname.
_dns_cache: dict = {}

async def _resolve(host: str) -> list:
    loop = asyncio.get_running_loop()
    now = loop.time()
    entry = _dns_cache.get(host)
    if entry and now < entry[1]:
        return entry[0]
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    addrs = [i[4][0] for i in infos if i[4][0]]
    if addrs:
        _dns_cache[host] = (addrs, now + 60.0)
    return addrs


_CONNECT_ESTABLISHED = b"HTTP/1.1 200 Connection established\r\n\r\n"
_BAD_GATEWAY = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n"
_BAD_REQUEST = b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n"
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


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> int:
    """One-directional byte relay until EOF or error. Returns bytes relayed."""
    total = 0
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
            total += len(data)
    except Exception:
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

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle_client, self._listen_ip, self._port,
            reuse_address=True, backlog=256,
        )
        addr = self._server.sockets[0].getsockname()
        auth_note = " (auth required)" if self._auth else ""
        upstream_note = f" → upstream {self._upstream}" if self._upstream else " → direct"
        logger.info("Forward proxy listening on %s:%d%s%s", addr[0], addr[1], upstream_note, auth_note)
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
        try:
            await asyncio.wait_for(self._process(reader, writer, peer), timeout=300)
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.debug("Forward proxy error from %s:%s — %s", peer[0], peer[1], e)
        finally:
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

        logger.debug("CONNECT %s from %s:%s", target, peer[0], peer[1])
        start = time.monotonic()
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

        t1 = asyncio.create_task(_relay(reader, remote_writer))
        t2 = asyncio.create_task(_relay(remote_reader, writer))
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

        # Read body
        body = b""
        content_length = int(headers.get("content-length", 0) or 0)
        if content_length > 0:
            try:
                body = await asyncio.wait_for(reader.read(content_length), timeout=30)
            except asyncio.TimeoutError:
                writer.write(_BAD_REQUEST)
                return

        logger.debug("HTTP %s %s:%d%s from %s:%s", method, host, port, path, peer[0], peer[1])
        start = time.monotonic()
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

        req_bytes = rebuilt.encode() + body
        remote_writer.write(req_bytes)
        await remote_writer.drain()

        # Relay response back to client
        resp_bytes = await _relay(remote_reader, writer)
        try:
            remote_writer.close()
            await remote_writer.wait_closed()
        except Exception:
            pass
        if self._on_connection:
            self._on_connection(_fwd_stat(peer[0], peer[1], f"{host}:{port}", "completed",
                                          time.monotonic() - start, len(req_bytes), resp_bytes))
