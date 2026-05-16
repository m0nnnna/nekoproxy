"""Async TCP Proxy implementation using asyncio.
Supports optional HTTP scraper detection (User-Agent) for aggressive firewall-style blocking.
"""

import asyncio
import logging
import time
from typing import Optional, Callable, Set, Dict, List, Awaitable, Any
from dataclasses import dataclass, field

from agent.config import settings

try:
    from python_socks.async_.asyncio import Proxy as _SocksProxy
    _SOCKS_AVAILABLE = True
except ImportError:
    _SocksProxy = None
    _SOCKS_AVAILABLE = False

logger = logging.getLogger(__name__)

# Default User-Agent substrings that indicate AI/scraper bots (block and report)
DEFAULT_SCRAPER_UA_PATTERNS = [
    "GPTBot",
    "ChatGPT-User",
    "Claude-Web",
    "ClaudeBot",
    "anthropic-ai",
    "Google-Extended",
    "Bytespider",
    "CCBot",
    "cohere-ai",
]


@dataclass
class ConnectionStats:
    """Statistics for a single connection."""
    client_ip: str
    client_port: int
    service_id: Optional[int]
    start_time: float = field(default_factory=time.time)
    bytes_sent: int = 0
    bytes_received: int = 0
    status: str = "connected"
    proxy_type: str = "incoming"
    target: Optional[str] = None

    @property
    def duration(self) -> float:
        return time.time() - self.start_time


class TCPProxy:
    """Async TCP proxy server for a single port."""

    def __init__(
        self,
        listen_port: int,
        backend_host: str,
        backend_port: int,
        service_id: int,
        service_name: str = "unknown",
        blocklist: Set[str] = None,
        on_connection: Optional[Callable[[ConnectionStats], None]] = None,
        scraper_ua_patterns: Optional[List[str]] = None,
        on_scraper_detected: Optional[Callable[[str], Awaitable[None]]] = None,
        rate_limiter: Optional[Any] = None,
        listen_ip: Optional[str] = None,
        geo_mode: str = "off",
        geo_countries: Optional[List[str]] = None,
        geo_lookup: Optional[Any] = None,
        idle_timeout_seconds: int = 0,
    ):
        self.listen_port = listen_port
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.service_id = service_id
        self.service_name = service_name
        self.blocklist = blocklist or set()
        self.on_connection = on_connection
        self.scraper_ua_patterns = scraper_ua_patterns or []
        self.on_scraper_detected = on_scraper_detected
        self.rate_limiter = rate_limiter
        self._listen_ip = listen_ip or settings.listen_ip
        self.geo_mode = geo_mode or "off"
        self.geo_countries = set((geo_countries or []))
        self.geo_lookup = geo_lookup
        self.idle_timeout_seconds = idle_timeout_seconds or 0

        self._server: Optional[asyncio.Server] = None
        self._active_connections: Dict[str, ConnectionStats] = {}
        self._running = False

    @property
    def active_connection_count(self) -> int:
        return len(self._active_connections)

    async def start(self):
        """Start the TCP proxy server."""
        self._running = True
        self._server = await asyncio.start_server(
            self._handle_client,
            self._listen_ip,
            self.listen_port,
            reuse_address=True
        )

        addr = self._server.sockets[0].getsockname()
        logger.info(
            f"[{self.service_name}] TCP proxy listening on {addr[0]}:{addr[1]} "
            f"-> {self.backend_host}:{self.backend_port}"
        )

        async with self._server:
            await self._server.serve_forever()

    async def stop(self):
        """Stop the TCP proxy server."""
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info(f"[{self.service_name}] TCP proxy stopped")

    def update_blocklist(self, blocklist: Set[str]):
        """Update the blocklist."""
        self.blocklist = blocklist

    def _is_http_scraper(self, data: bytes) -> bool:
        """Return True if data looks like HTTP and User-Agent matches a scraper pattern."""
        if not data or not self.scraper_ua_patterns:
            return False
        methods = (b"GET ", b"POST ", b"HEAD ", b"PUT ", b"DELETE ", b"PATCH ", b"OPTIONS ")
        if not any(data.startswith(m) for m in methods):
            return False
        # Find User-Agent line (simplified: case-insensitive search)
        ua_key = b"user-agent:"
        idx = data.lower().find(ua_key)
        if idx == -1:
            return False
        start = idx + len(ua_key)
        end = data.find(b"\r\n", start)
        if end == -1:
            end = len(data)
        ua_value = data[start:end].strip().decode("utf-8", errors="ignore")
        return any(p.lower() in ua_value.lower() for p in self.scraper_ua_patterns)

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter
    ):
        """Handle a new client connection."""
        client_addr = writer.get_extra_info('peername')
        client_ip, client_port = client_addr

        # Create connection ID and stats
        conn_id = f"{client_ip}:{client_port}"
        stats = ConnectionStats(
            client_ip=client_ip,
            client_port=client_port,
            service_id=self.service_id
        )
        self._active_connections[conn_id] = stats

        # Check blocklist
        if client_ip in self.blocklist:
            logger.warning(f"[{self.service_name}] BLOCKED connection from {conn_id}")
            stats.status = "blocked"
            writer.close()
            await writer.wait_closed()
            del self._active_connections[conn_id]
            if self.on_connection:
                self.on_connection(stats)
            return

        # Geo allowlist/blocklist (optional)
        if self.geo_mode != "off" and self.geo_lookup and self.geo_countries:
            country = self.geo_lookup.country_code(client_ip)
            if self.geo_mode == "allowlist" and country not in self.geo_countries:
                logger.warning(f"[{self.service_name}] GEO BLOCK (allowlist) connection from {conn_id} (country={country or '??'})")
                stats.status = "blocked"
                writer.close()
                await writer.wait_closed()
                del self._active_connections[conn_id]
                if self.on_connection:
                    self.on_connection(stats)
                return
            if self.geo_mode == "blocklist" and country and country in self.geo_countries:
                logger.warning(f"[{self.service_name}] GEO BLOCK (blocklist) connection from {conn_id} (country={country})")
                stats.status = "blocked"
                writer.close()
                await writer.wait_closed()
                del self._active_connections[conn_id]
                if self.on_connection:
                    self.on_connection(stats)
                return

        # Rate limiting (drop + optional auto-block)
        if self.rate_limiter:
            if not await self.rate_limiter.allow(client_ip, self.service_id):
                logger.warning(f"[{self.service_name}] RATE LIMIT connection from {conn_id}")
                stats.status = "blocked"
                writer.close()
                await writer.wait_closed()
                del self._active_connections[conn_id]
                if self.on_connection:
                    self.on_connection(stats)
                return

        # Optional: peek first chunk for HTTP scraper User-Agent (AI bots)
        first_chunk: Optional[bytes] = None
        if self.scraper_ua_patterns and self.on_scraper_detected:
            try:
                first_chunk = await asyncio.wait_for(reader.read(settings.buffer_size), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            if first_chunk and self._is_http_scraper(first_chunk):
                logger.warning(f"[{self.service_name}] BLOCKED scraper from {conn_id}")
                stats.status = "blocked"
                writer.close()
                await writer.wait_closed()
                del self._active_connections[conn_id]
                if self.on_connection:
                    self.on_connection(stats)
                try:
                    await self.on_scraper_detected(client_ip)
                except Exception as e:
                    logger.warning(f"Scraper callback error: {e}")
                return

        logger.info(f"[{self.service_name}] Connection from {conn_id}")

        backend_reader: Optional[asyncio.StreamReader] = None
        backend_writer: Optional[asyncio.StreamWriter] = None

        try:
            # Connect to backend (optionally via upstream proxy)
            if settings.upstream_proxy and _SOCKS_AVAILABLE:
                proxy = _SocksProxy.from_url(settings.upstream_proxy)
                sock = await asyncio.wait_for(
                    proxy.connect(dest_host=self.backend_host, dest_port=self.backend_port),
                    timeout=settings.connection_timeout,
                )
                backend_reader, backend_writer = await asyncio.open_connection(sock=sock)
            elif settings.upstream_proxy and not _SOCKS_AVAILABLE:
                raise RuntimeError("NEKO_AGENT_UPSTREAM_PROXY is set but python-socks is not installed")
            else:
                backend_reader, backend_writer = await asyncio.wait_for(
                    asyncio.open_connection(self.backend_host, self.backend_port),
                    timeout=settings.connection_timeout,
                )

            # If we peeked a chunk, send it first
            if first_chunk:
                backend_writer.write(first_chunk)
                await backend_writer.drain()

            logger.info(
                f"[{self.service_name}] Forwarding {conn_id} -> "
                f"{self.backend_host}:{self.backend_port}"
            )

            # Create bidirectional forwarding tasks (idle_timeout applied in _forward_data)
            c2b = asyncio.create_task(
                self._forward_data(reader, backend_writer, stats, "c2b")
            )
            b2c = asyncio.create_task(
                self._forward_data(backend_reader, writer, stats, "b2c")
            )

            # Wait for either direction to complete
            done, pending = await asyncio.wait(
                [c2b, b2c],
                return_when=asyncio.FIRST_COMPLETED
            )

            # Cancel pending tasks
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            stats.status = "completed"

        except asyncio.TimeoutError:
            logger.error(f"[{self.service_name}] Timeout connecting to backend for {conn_id}")
            stats.status = "timeout"
        except ConnectionRefusedError:
            logger.error(f"[{self.service_name}] Backend refused connection for {conn_id}")
            stats.status = "refused"
        except Exception as e:
            logger.error(f"[{self.service_name}] Error handling {conn_id}: {e}")
            stats.status = "error"
        finally:
            # Clean up connections
            writer.close()
            await writer.wait_closed()
            if backend_writer:
                backend_writer.close()
                await backend_writer.wait_closed()

            del self._active_connections[conn_id]

            logger.info(
                f"[{self.service_name}] Closed {conn_id} "
                f"(duration: {stats.duration:.2f}s, "
                f"sent: {stats.bytes_sent}, recv: {stats.bytes_received})"
            )

            if self.on_connection:
                self.on_connection(stats)

    async def _forward_data(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        stats: ConnectionStats,
        direction: str
    ):
        """Forward data from reader to writer. Closes on idle if idle_timeout_seconds > 0."""
        try:
            while True:
                if self.idle_timeout_seconds > 0:
                    try:
                        data = await asyncio.wait_for(
                            reader.read(settings.buffer_size),
                            timeout=float(self.idle_timeout_seconds),
                        )
                    except asyncio.TimeoutError:
                        break  # idle timeout, close connection
                else:
                    data = await reader.read(settings.buffer_size)
                if not data:
                    break
                writer.write(data)
                await writer.drain()

                # Track bytes
                if direction == "c2b":
                    stats.bytes_sent += len(data)
                else:
                    stats.bytes_received += len(data)

        except (ConnectionResetError, BrokenPipeError):
            pass
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.debug(f"Forward error ({direction}): {e}")

    def set_geo(self, geo_mode: str, geo_countries: List[str], geo_lookup: Optional[Any] = None) -> None:
        """Update geo filtering (allowlist/blocklist)."""
        self.geo_mode = geo_mode or "off"
        self.geo_countries = set(geo_countries or [])
        self.geo_lookup = geo_lookup

    def set_idle_timeout(self, seconds: int) -> None:
        """Update idle connection timeout; 0 = disabled."""
        self.idle_timeout_seconds = max(0, int(seconds))


class TCPProxyManager:
    """Manages multiple TCP proxy instances."""

    def __init__(
        self,
        on_connection: Optional[Callable[[ConnectionStats], None]] = None,
        scraper_ua_patterns: Optional[List[str]] = None,
        on_scraper_detected: Optional[Callable[[str], Awaitable[None]]] = None,
        rate_limiter: Optional[Any] = None,
        listen_ip: Optional[str] = None,
        geo_mode: str = "off",
        geo_countries: Optional[List[str]] = None,
        geo_lookup: Optional[Any] = None,
        idle_timeout_seconds: int = 0,
    ):
        self.on_connection = on_connection
        self.scraper_ua_patterns = scraper_ua_patterns or []
        self.on_scraper_detected = on_scraper_detected
        self.rate_limiter = rate_limiter
        self._listen_ip = listen_ip or settings.listen_ip
        self._geo_mode = geo_mode or "off"
        self._geo_countries = list(geo_countries or [])
        self._geo_lookup = geo_lookup
        self._idle_timeout_seconds = idle_timeout_seconds or 0
        self._proxies: Dict[int, TCPProxy] = {}  # port -> proxy
        self._tasks: Dict[int, asyncio.Task] = {}
        self._blocklist: Set[str] = set()

    def set_listen_ip(self, ip: str) -> None:
        """Set listen IP for new proxies (e.g. WireGuard-only binding)."""
        self._listen_ip = ip

    def set_geo(self, geo_mode: str, geo_countries: List[str], geo_lookup: Optional[Any] = None) -> None:
        """Set geo filtering for new and existing proxies."""
        self._geo_mode = geo_mode or "off"
        self._geo_countries = list(geo_countries or [])
        self._geo_lookup = geo_lookup
        for proxy in self._proxies.values():
            proxy.set_geo(geo_mode, geo_countries, geo_lookup)

    def set_idle_timeout(self, seconds: int) -> None:
        """Set idle connection timeout for new and existing proxies; 0 = disabled."""
        self._idle_timeout_seconds = max(0, int(seconds))
        for proxy in self._proxies.values():
            proxy.set_idle_timeout(seconds)

    @property
    def active_connections(self) -> int:
        return sum(p.active_connection_count for p in self._proxies.values())

    def update_blocklist(self, blocklist: List[str]):
        """Update blocklist for all proxies."""
        self._blocklist = set(blocklist)
        for proxy in self._proxies.values():
            proxy.update_blocklist(self._blocklist)

    async def add_proxy(
        self,
        listen_port: int,
        backend_host: str,
        backend_port: int,
        service_id: int,
        service_name: str = "unknown"
    ):
        """Add and start a new TCP proxy."""
        if listen_port in self._proxies:
            logger.warning(f"Proxy already running on port {listen_port}")
            return

        proxy = TCPProxy(
            listen_port=listen_port,
            backend_host=backend_host,
            backend_port=backend_port,
            service_id=service_id,
            service_name=service_name,
            blocklist=self._blocklist,
            on_connection=self.on_connection,
            scraper_ua_patterns=self.scraper_ua_patterns,
            on_scraper_detected=self.on_scraper_detected,
            rate_limiter=self.rate_limiter,
            listen_ip=self._listen_ip,
            geo_mode=self._geo_mode,
            geo_countries=self._geo_countries,
            geo_lookup=self._geo_lookup,
            idle_timeout_seconds=self._idle_timeout_seconds,
        )

        self._proxies[listen_port] = proxy
        self._tasks[listen_port] = asyncio.create_task(proxy.start())

    async def remove_proxy(self, listen_port: int):
        """Stop and remove a TCP proxy."""
        if listen_port not in self._proxies:
            return

        proxy = self._proxies[listen_port]
        await proxy.stop()

        task = self._tasks.get(listen_port)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        del self._proxies[listen_port]
        del self._tasks[listen_port]

    async def sync_proxies(self, rules: List[dict]):
        """Synchronize proxies with a list of forwarding rules."""
        # Build set of desired ports
        desired_ports = {r['listen_port'] for r in rules if r.get('protocol') == 'tcp'}

        # Remove proxies for deleted rules
        for port in list(self._proxies.keys()):
            if port not in desired_ports:
                logger.info(f"Removing proxy for port {port}")
                await self.remove_proxy(port)

        # Add/update proxies for new rules
        for rule in rules:
            if rule.get('protocol') != 'tcp':
                continue

            port = rule['listen_port']
            if port not in self._proxies:
                logger.info(f"Adding TCP proxy for port {port}")
                await self.add_proxy(
                    listen_port=port,
                    backend_host=rule['backend_host'],
                    backend_port=rule['backend_port'],
                    service_id=rule['service_id'],
                    service_name=rule.get('service_name', 'unknown')
                )

    async def stop_all(self):
        """Stop all proxies."""
        for port in list(self._proxies.keys()):
            await self.remove_proxy(port)
