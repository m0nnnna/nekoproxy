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
from agent.core.firewall import FirewallManager
from agent.core.control_api import ControlAPI
from agent.core.email_proxy import EmailProxyManager
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

        # Firewall manager
        self._firewall_manager = FirewallManager()

        # Email proxy manager
        self._email_manager = EmailProxyManager()

        # Controller communication
        self._heartbeat: Optional[HeartbeatSender] = None
        self._config_sync: Optional[ConfigSync] = None
        self._control_api: Optional[ControlAPI] = None

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

        # Sync firewall rules
        await self._firewall_manager.sync_rules(config.firewall_rules)

        # Apply email config if present and email proxy is deployed
        if config.email_config and self._email_manager.is_deployed:
            await self._email_manager.apply_config(config.email_config)

        logger.info(
            f"Config applied: {len([s for s in services if s['protocol'] == 'tcp'])} TCP services, "
            f"{len([s for s in services if s['protocol'] == 'udp'])} UDP services, "
            f"{len(config.blocklist)} blocked IPs, "
            f"{len(config.firewall_rules)} firewall rules"
        )

    async def _deploy_email(self, hostname: str, mailcow_ip: str, mailcow_port: int, proxy_ip: str) -> tuple:
        """Deploy email proxy (Postfix + SASL, no rspamd - mailcow handles filtering).

        Called by ControlAPI when controller requests deployment.

        Args:
            hostname: FQDN for Postfix myhostname and Let's Encrypt SSL cert
            mailcow_ip: Mailcow's internal/WireGuard IP for transport routing
            mailcow_port: Mailcow SMTP port
            proxy_ip: This agent's public IP for header stamping

        Returns:
            Tuple of (success: bool, error_message: str or None)
        """
        return await self._email_manager.deploy(hostname, mailcow_ip, mailcow_port, proxy_ip)

    async def _trigger_email_sync(self):
        """Trigger email configuration sync from controller.

        Called by ControlAPI when controller requests email config refresh.
        """
        if not self._email_manager.is_deployed:
            logger.warning("Email proxy not deployed, skipping email sync")
            return

        # Force a full config sync which will include email config
        if self._config_sync:
            await self._config_sync.force_sync()

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

        # Initialize firewall manager
        await self._firewall_manager.initialize()

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

        # Start control API (for receiving push notifications)
        self._control_api = ControlAPI(
            trigger_sync=self._config_sync.force_sync,
            deploy_email=self._deploy_email,
            trigger_email_sync=self._trigger_email_sync
        )
        await self._control_api.start()

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
        if self._control_api:
            await self._control_api.stop()

        if self._config_sync:
            await self._config_sync.stop()

        if self._heartbeat:
            await self._heartbeat.stop()

        await self._tcp_manager.stop_all()
        await self._udp_manager.stop_all()

        # Clean up firewall rules
        await self._firewall_manager.shutdown()

        # Stop email proxy if deployed
        await self._email_manager.shutdown()

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
