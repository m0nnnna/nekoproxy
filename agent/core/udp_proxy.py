"""Async UDP Proxy implementation using asyncio."""

import asyncio
import logging
import time
from typing import Optional, Callable, Set, Dict, List, Tuple
from dataclasses import dataclass, field

from agent.config import settings

logger = logging.getLogger(__name__)


@dataclass
class UDPConnectionStats:
    """Statistics for a UDP client session."""
    client_ip: str
    client_port: int
    service_id: int
    start_time: float = field(default_factory=time.time)
    bytes_sent: int = 0
    bytes_received: int = 0
    packets_sent: int = 0
    packets_received: int = 0
    last_activity: float = field(default_factory=time.time)
    status: str = "active"

    @property
    def duration(self) -> float:
        return time.time() - self.start_time


class UDPProxyProtocol(asyncio.DatagramProtocol):
    """UDP protocol handler for proxying datagrams."""

    def __init__(
        self,
        backend_host: str,
        backend_port: int,
        service_id: int,
        service_name: str,
        blocklist: Set[str],
        on_connection: Optional[Callable],
        client_timeout: int = 300  # 5 minutes
    ):
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.service_id = service_id
        self.service_name = service_name
        self.blocklist = blocklist
        self.on_connection = on_connection
        self.client_timeout = client_timeout

        self.transport: Optional[asyncio.DatagramTransport] = None
        # Map client address to (backend transport, stats)
        self._clients: Dict[tuple, Tuple[asyncio.DatagramTransport, UDPConnectionStats]] = {}
        self._cleanup_task: Optional[asyncio.Task] = None

    def connection_made(self, transport: asyncio.DatagramTransport):
        self.transport = transport
        addr = transport.get_extra_info('sockname')
        logger.info(
            f"[{self.service_name}] UDP proxy listening on {addr[0]}:{addr[1]} "
            f"-> {self.backend_host}:{self.backend_port}"
        )
        # Start cleanup task
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    def connection_lost(self, exc):
        if self._cleanup_task:
            self._cleanup_task.cancel()
        # Close all backend connections
        for backend_transport, stats in self._clients.values():
            stats.status = "closed"
            backend_transport.close()
            if self.on_connection:
                self.on_connection(stats)
        self._clients.clear()

    def datagram_received(self, data: bytes, addr: tuple):
        """Handle incoming datagram from client."""
        client_ip, client_port = addr

        # Check blocklist
        if client_ip in self.blocklist:
            logger.debug(f"[{self.service_name}] Blocked UDP packet from {client_ip}")
            return

        if addr not in self._clients:
            # New client - create backend connection
            asyncio.create_task(self._create_backend_connection(addr, data))
        else:
            # Existing client - forward to backend
            backend_transport, stats = self._clients[addr]
            backend_transport.sendto(data, (self.backend_host, self.backend_port))
            stats.bytes_sent += len(data)
            stats.packets_sent += 1
            stats.last_activity = time.time()

    async def _create_backend_connection(self, client_addr: tuple, initial_data: bytes):
        """Create a new backend connection for a client."""
        client_ip, client_port = client_addr

        try:
            loop = asyncio.get_event_loop()

            # Create backend protocol
            backend_protocol = BackendUDPProtocol(
                self.transport,
                client_addr,
                self.service_name
            )

            # Connect to backend
            backend_transport, _ = await loop.create_datagram_endpoint(
                lambda: backend_protocol,
                remote_addr=(self.backend_host, self.backend_port)
            )

            # Create stats
            stats = UDPConnectionStats(
                client_ip=client_ip,
                client_port=client_port,
                service_id=self.service_id
            )

            # Store client mapping
            self._clients[client_addr] = (backend_transport, stats)
            backend_protocol.stats = stats

            # Send initial data
            backend_transport.sendto(initial_data, (self.backend_host, self.backend_port))
            stats.bytes_sent += len(initial_data)
            stats.packets_sent += 1

            logger.info(
                f"[{self.service_name}] New UDP client {client_ip}:{client_port}"
            )

        except Exception as e:
            logger.error(
                f"[{self.service_name}] Error creating backend connection for {client_addr}: {e}"
            )

    async def _cleanup_loop(self):
        """Periodically clean up inactive clients."""
        while True:
            await asyncio.sleep(60)  # Check every minute

            now = time.time()
            to_remove = []

            for addr, (backend_transport, stats) in self._clients.items():
                if now - stats.last_activity > self.client_timeout:
                    to_remove.append(addr)

            for addr in to_remove:
                backend_transport, stats = self._clients.pop(addr)
                stats.status = "timeout"
                backend_transport.close()
                logger.info(
                    f"[{self.service_name}] Cleaned up inactive UDP client "
                    f"{stats.client_ip}:{stats.client_port}"
                )
                if self.on_connection:
                    self.on_connection(stats)


class BackendUDPProtocol(asyncio.DatagramProtocol):
    """Protocol for receiving data from backend and forwarding to client."""

    def __init__(
        self,
        client_transport: asyncio.DatagramTransport,
        client_addr: tuple,
        service_name: str
    ):
        self.client_transport = client_transport
        self.client_addr = client_addr
        self.service_name = service_name
        self.stats: Optional[UDPConnectionStats] = None

    def datagram_received(self, data: bytes, addr: tuple):
        """Receive from backend, forward to client."""
        self.client_transport.sendto(data, self.client_addr)
        if self.stats:
            self.stats.bytes_received += len(data)
            self.stats.packets_received += 1
            self.stats.last_activity = time.time()

    def error_received(self, exc):
        logger.error(f"[{self.service_name}] Backend error: {exc}")


class UDPProxy:
    """UDP proxy server for a single port."""

    def __init__(
        self,
        listen_port: int,
        backend_host: str,
        backend_port: int,
        service_id: int,
        service_name: str = "unknown",
        blocklist: Set[str] = None,
        on_connection: Optional[Callable] = None
    ):
        self.listen_port = listen_port
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.service_id = service_id
        self.service_name = service_name
        self.blocklist = blocklist or set()
        self.on_connection = on_connection

        self._transport: Optional[asyncio.DatagramTransport] = None
        self._protocol: Optional[UDPProxyProtocol] = None

    @property
    def active_connection_count(self) -> int:
        if self._protocol:
            return len(self._protocol._clients)
        return 0

    def update_blocklist(self, blocklist: Set[str]):
        """Update the blocklist."""
        self.blocklist = blocklist
        if self._protocol:
            self._protocol.blocklist = blocklist

    async def start(self):
        """Start the UDP proxy."""
        loop = asyncio.get_event_loop()

        self._transport, self._protocol = await loop.create_datagram_endpoint(
            lambda: UDPProxyProtocol(
                backend_host=self.backend_host,
                backend_port=self.backend_port,
                service_id=self.service_id,
                service_name=self.service_name,
                blocklist=self.blocklist,
                on_connection=self.on_connection
            ),
            local_addr=(settings.listen_ip, self.listen_port)
        )

    async def stop(self):
        """Stop the UDP proxy."""
        if self._transport:
            self._transport.close()
            logger.info(f"[{self.service_name}] UDP proxy stopped")


class UDPProxyManager:
    """Manages multiple UDP proxy instances."""

    def __init__(self, on_connection: Optional[Callable] = None):
        self.on_connection = on_connection
        self._proxies: Dict[int, UDPProxy] = {}  # port -> proxy
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
        """Add and start a new UDP proxy."""
        if listen_port in self._proxies:
            logger.warning(f"UDP proxy already running on port {listen_port}")
            return

        proxy = UDPProxy(
            listen_port=listen_port,
            backend_host=backend_host,
            backend_port=backend_port,
            service_id=service_id,
            service_name=service_name,
            blocklist=self._blocklist,
            on_connection=self.on_connection
        )

        await proxy.start()
        self._proxies[listen_port] = proxy

    async def remove_proxy(self, listen_port: int):
        """Stop and remove a UDP proxy."""
        if listen_port not in self._proxies:
            return

        proxy = self._proxies[listen_port]
        await proxy.stop()
        del self._proxies[listen_port]

    async def sync_proxies(self, rules: List[dict]):
        """Synchronize proxies with a list of forwarding rules."""
        desired_ports = {r['listen_port'] for r in rules if r.get('protocol') == 'udp'}

        # Remove proxies for deleted rules
        for port in list(self._proxies.keys()):
            if port not in desired_ports:
                logger.info(f"Removing UDP proxy for port {port}")
                await self.remove_proxy(port)

        # Add proxies for new rules
        for rule in rules:
            if rule.get('protocol') != 'udp':
                continue

            port = rule['listen_port']
            if port not in self._proxies:
                logger.info(f"Adding UDP proxy for port {port}")
                await self.add_proxy(
                    listen_port=port,
                    backend_host=rule['resolved_backend_host'],
                    backend_port=rule['resolved_backend_port'],
                    service_id=rule['service_id'],
                    service_name=rule.get('service_name', 'unknown')
                )

    async def stop_all(self):
        """Stop all proxies."""
        for port in list(self._proxies.keys()):
            await self.remove_proxy(port)
