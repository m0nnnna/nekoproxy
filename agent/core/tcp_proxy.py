"""Async TCP Proxy implementation using asyncio."""

import asyncio
import logging
import time
from typing import Optional, Callable, Set, Dict, List
from dataclasses import dataclass, field

from agent.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ConnectionStats:
    """Statistics for a single connection."""
    client_ip: str
    client_port: int
    service_id: int
    start_time: float = field(default_factory=time.time)
    bytes_sent: int = 0
    bytes_received: int = 0
    status: str = "connected"

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
        on_connection: Optional[Callable[[ConnectionStats], None]] = None
    ):
        self.listen_port = listen_port
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.service_id = service_id
        self.service_name = service_name
        self.blocklist = blocklist or set()
        self.on_connection = on_connection

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
            settings.listen_ip,
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

        logger.info(f"[{self.service_name}] Connection from {conn_id}")

        backend_reader: Optional[asyncio.StreamReader] = None
        backend_writer: Optional[asyncio.StreamWriter] = None

        try:
            # Connect to backend
            backend_reader, backend_writer = await asyncio.wait_for(
                asyncio.open_connection(self.backend_host, self.backend_port),
                timeout=settings.connection_timeout
            )

            logger.info(
                f"[{self.service_name}] Forwarding {conn_id} -> "
                f"{self.backend_host}:{self.backend_port}"
            )

            # Create bidirectional forwarding tasks
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
        """Forward data from reader to writer."""
        try:
            while True:
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
        except Exception as e:
            logger.debug(f"Forward error ({direction}): {e}")


class TCPProxyManager:
    """Manages multiple TCP proxy instances."""

    def __init__(self, on_connection: Optional[Callable[[ConnectionStats], None]] = None):
        self.on_connection = on_connection
        self._proxies: Dict[int, TCPProxy] = {}  # port -> proxy
        self._tasks: Dict[int, asyncio.Task] = {}
        self._blocklist: Set[str] = set()

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
            on_connection=self.on_connection
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
