"""Main entry point for NekoProxy agent."""

import asyncio
import logging
import signal
import sys
from typing import Optional

import httpx

from agent.config import settings
from agent.core.tcp_proxy import TCPProxyManager, ConnectionStats
from agent.core.udp_proxy import UDPProxyManager
from agent.core.heartbeat import HeartbeatSender
from agent.core.config_sync import ConfigSync
from agent.core.stats_reporter import StatsReporter
from shared.models import AgentConfig, AgentRegistration

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class NekoProxyAgent:
    """Main NekoProxy agent that coordinates all components."""

    def __init__(self):
        self.agent_id: Optional[int] = None
        self._running = False

        # Stats reporter (initialized after registration)
        self._stats_reporter: Optional[StatsReporter] = None

        # Proxy managers
        self._tcp_manager = TCPProxyManager(on_connection=self._on_connection)
        self._udp_manager = UDPProxyManager(on_connection=self._on_connection)

        # Controller communication
        self._heartbeat: Optional[HeartbeatSender] = None
        self._config_sync: Optional[ConfigSync] = None

    def _on_connection(self, stats):
        """Called when a connection completes."""
        if self._stats_reporter:
            self._stats_reporter.record(stats)

    def _get_active_connections(self) -> int:
        """Get total active connection count."""
        return self._tcp_manager.active_connections + self._udp_manager.active_connections

    async def _on_config_update(self, config: AgentConfig):
        """Handle configuration updates from controller."""
        logger.info(f"Applying config version {config.config_version}")

        # Update blocklist
        self._tcp_manager.update_blocklist(config.blocklist)
        self._udp_manager.update_blocklist(config.blocklist)

        # Convert services to dict format for proxy managers
        services = [
            {
                "listen_port": s.listen_port,
                "protocol": s.protocol.value,
                "backend_host": s.backend_host,
                "backend_port": s.backend_port,
                "service_id": s.id,
                "service_name": s.name
            }
            for s in config.services
        ]

        # Sync proxies with new services
        await self._tcp_manager.sync_proxies(services)
        await self._udp_manager.sync_proxies(services)

        logger.info(
            f"Config applied: {len([s for s in services if s['protocol'] == 'tcp'])} TCP services, "
            f"{len([s for s in services if s['protocol'] == 'udp'])} UDP services, "
            f"{len(config.blocklist)} blocked IPs"
        )

    async def register(self) -> bool:
        """Register with the controller."""
        registration = AgentRegistration(
            hostname=settings.hostname,
            wireguard_ip=settings.wireguard_ip,
            public_ip=settings.public_ip,
            version=settings.version
        )

        url = f"{settings.controller_url}/api/v1/agents/register"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=registration.model_dump())
                response.raise_for_status()
                data = response.json()
                self.agent_id = data["id"]
                logger.info(
                    f"Registered with controller as agent {self.agent_id} "
                    f"({settings.hostname} @ {settings.wireguard_ip})"
                )
                return True
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to controller: {e}")
            return False
        except httpx.HTTPStatusError as e:
            logger.error(f"Registration failed: {e.response.text}")
            return False

    async def start(self):
        """Start the NekoProxy agent."""
        logger.info("=" * 70)
        logger.info("NekoProxy Agent Starting")
        logger.info(f"  Hostname: {settings.hostname}")
        logger.info(f"  WireGuard IP: {settings.wireguard_ip}")
        logger.info(f"  Controller: {settings.controller_url}")
        logger.info("=" * 70)

        # Register with controller
        if not await self.register():
            logger.error("Failed to register with controller - exiting")
            return

        self._running = True

        # Initialize stats reporter
        self._stats_reporter = StatsReporter(self.agent_id)
        await self._stats_reporter.start()

        # Start heartbeat
        self._heartbeat = HeartbeatSender(
            agent_id=self.agent_id,
            get_active_connections=self._get_active_connections
        )
        await self._heartbeat.start()

        # Start config sync (will apply initial config)
        self._config_sync = ConfigSync(
            agent_id=self.agent_id,
            on_config_update=lambda c: asyncio.create_task(self._on_config_update(c))
        )
        await self._config_sync.start()

        logger.info("=" * 70)
        logger.info("NekoProxy Agent running. Press Ctrl+C to stop.")
        logger.info("=" * 70)

        # Keep running until stopped
        while self._running:
            await asyncio.sleep(1)

    async def stop(self):
        """Stop the NekoProxy agent."""
        logger.info("Stopping NekoProxy agent...")
        self._running = False

        # Stop components in order
        if self._config_sync:
            await self._config_sync.stop()

        if self._heartbeat:
            await self._heartbeat.stop()

        await self._tcp_manager.stop_all()
        await self._udp_manager.stop_all()

        if self._stats_reporter:
            await self._stats_reporter.stop()

        logger.info("NekoProxy agent stopped")


async def main():
    """Main entry point."""
    agent = NekoProxyAgent()

    # Setup signal handlers
    loop = asyncio.get_event_loop()

    def signal_handler():
        logger.info("Received shutdown signal")
        asyncio.create_task(agent.stop())

    # Handle SIGTERM and SIGINT
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    try:
        await agent.start()
    except KeyboardInterrupt:
        await agent.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
