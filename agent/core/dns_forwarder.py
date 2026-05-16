"""DNS forwarding server.

Listens on UDP and TCP (same port) and forwards every DNS query byte-for-byte
to the configured upstream resolver. No DNS parsing — pure relay.

Typical use: point devices at the agent IP on port 53 (requires root) or any
unprivileged port (e.g. 5353). The upstream can be any standard DNS server,
e.g. "1.1.1.1:53", "8.8.8.8", "9.9.9.9:53".
"""

import asyncio
import logging
import socket as _socket
import time
import types
from typing import Optional, Tuple, Callable

logger = logging.getLogger(__name__)

_UDP_BUF = 4096
_TIMEOUT = 5.0


def _parse_upstream(upstream: str) -> Tuple[str, int]:
    """Return (host, port) from 'host' or 'host:port'. Default port: 53."""
    upstream = upstream.strip()
    if ":" in upstream:
        host, _, port_str = upstream.rpartition(":")
        try:
            return host.strip(), int(port_str)
        except ValueError:
            pass
    return upstream, 53


async def _query_upstream_udp(query: bytes, host: str, port: int) -> Optional[bytes]:
    """Send a DNS query to the upstream over UDP and return the response."""
    loop = asyncio.get_running_loop()
    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    sock.setblocking(False)
    try:
        sock.connect((host, port))
        await loop.sock_sendall(sock, query)
        return await asyncio.wait_for(loop.sock_recv(sock, _UDP_BUF), timeout=_TIMEOUT)
    except Exception:
        return None
    finally:
        sock.close()


def _dns_stat(client_ip: str, client_port: int, status: str, duration: float,
              bytes_sent: int, bytes_received: int):
    return types.SimpleNamespace(
        client_ip=client_ip,
        client_port=client_port,
        service_id=None,
        status=status,
        duration=duration,
        bytes_sent=bytes_sent,
        bytes_received=bytes_received,
        proxy_type="dns",
        target=None,
    )


async def _handle_tcp_dns(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    upstream_host: str,
    upstream_port: int,
    on_connection: Optional[Callable] = None,
):
    """Handle a single DNS-over-TCP connection (2-byte length-prefixed messages)."""
    peer = writer.get_extra_info("peername", ("?", 0))
    start = time.monotonic()
    query_size = 0
    resp_size = 0
    status = "failed"
    try:
        length_bytes = await asyncio.wait_for(reader.readexactly(2), timeout=_TIMEOUT)
        length = int.from_bytes(length_bytes, "big")
        if length == 0 or length > 65535:
            return
        query = await asyncio.wait_for(reader.readexactly(length), timeout=_TIMEOUT)
        query_size = len(query)

        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(upstream_host, upstream_port), timeout=_TIMEOUT
        )
        try:
            upstream_writer.write(length_bytes + query)
            await upstream_writer.drain()

            resp_len_bytes = await asyncio.wait_for(upstream_reader.readexactly(2), timeout=_TIMEOUT)
            resp_length = int.from_bytes(resp_len_bytes, "big")
            if resp_length == 0 or resp_length > 65535:
                return
            response = await asyncio.wait_for(upstream_reader.readexactly(resp_length), timeout=_TIMEOUT)
            resp_size = len(response)

            writer.write(resp_len_bytes + response)
            await writer.drain()
            status = "resolved"
        finally:
            upstream_writer.close()
            try:
                await upstream_writer.wait_closed()
            except Exception:
                pass
    except Exception as e:
        logger.debug("DNS TCP error from %s:%s — %s", peer[0], peer[1], e)
    finally:
        if on_connection:
            on_connection(_dns_stat(
                peer[0], peer[1], status,
                time.monotonic() - start, query_size, resp_size,
            ))
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


class _DnsUdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, upstream_host: str, upstream_port: int,
                 on_connection: Optional[Callable] = None):
        self._host = upstream_host
        self._port = upstream_port
        self._on_connection = on_connection
        self._transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport: asyncio.DatagramTransport):
        self._transport = transport

    def datagram_received(self, data: bytes, addr: tuple):
        asyncio.create_task(self._forward(data, addr))

    def error_received(self, exc: Exception):
        logger.debug("DNS UDP error: %s", exc)

    def connection_lost(self, exc: Optional[Exception]):
        pass

    async def _forward(self, query: bytes, client_addr: tuple):
        start = time.monotonic()
        response = await _query_upstream_udp(query, self._host, self._port)
        elapsed = time.monotonic() - start
        if response and self._transport and not self._transport.is_closing():
            self._transport.sendto(response, client_addr)
        if self._on_connection:
            self._on_connection(_dns_stat(
                client_addr[0], client_addr[1],
                "resolved" if response else "failed",
                elapsed, len(query), len(response) if response else 0,
            ))


class DnsForwarder:
    """UDP + TCP DNS forwarder. Relays queries to a single upstream resolver."""

    def __init__(self, listen_ip: str, port: int, upstream: str,
                 on_connection: Optional[Callable] = None):
        self._listen_ip = listen_ip
        self._port = port
        self._upstream = upstream  # stored as-is for change detection
        self._upstream_host, self._upstream_port = _parse_upstream(upstream)
        self._on_connection = on_connection
        self._udp_transport: Optional[asyncio.DatagramTransport] = None
        self._tcp_server: Optional[asyncio.Server] = None

    async def start(self):
        loop = asyncio.get_running_loop()

        self._udp_transport, _ = await loop.create_datagram_endpoint(
            lambda: _DnsUdpProtocol(self._upstream_host, self._upstream_port, self._on_connection),
            local_addr=(self._listen_ip, self._port),
        )

        self._tcp_server = await asyncio.start_server(
            lambda r, w: _handle_tcp_dns(r, w, self._upstream_host, self._upstream_port, self._on_connection),
            self._listen_ip,
            self._port,
            reuse_address=True,
        )

        logger.info(
            "DNS forwarder listening on %s:%d → %s:%d (UDP+TCP)",
            self._listen_ip, self._port, self._upstream_host, self._upstream_port,
        )

        async with self._tcp_server:
            await self._tcp_server.serve_forever()

    async def stop(self):
        if self._udp_transport:
            self._udp_transport.close()
            self._udp_transport = None
        if self._tcp_server:
            self._tcp_server.close()
            await self._tcp_server.wait_closed()
            self._tcp_server = None
        logger.info("DNS forwarder stopped")
