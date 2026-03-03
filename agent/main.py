"""Main entry point for NekoProxy agent."""

import asyncio
import logging
import signal
import sys
from typing import Optional

import httpx

from agent.config import settings
from agent.core.tcp_proxy import TCPProxyManager, ConnectionStats, DEFAULT_SCRAPER_UA_PATTERNS
from agent.core.udp_proxy import UDPProxyManager
from agent.core.rate_limiter import RateLimiter
from agent.core.heartbeat import HeartbeatSender
from agent.core.config_sync import ConfigSync
from agent.core.stats_reporter import StatsReporter
from agent.core.firewall import FirewallManager
from agent.core.control_api import ControlAPI
from agent.core.email_proxy import EmailProxyManager
from agent.core.email_stats import EmailStatsCollector
from agent.core.security_monitor import SecurityMonitor
from agent.core.iptables_monitor import IptablesMonitor
from agent.core.geo import GeoLookup
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

        # Current blocklist (controller + local auto-blocks) for proxy and firewall
        self._current_blocklist: set = set()

        # Stats reporter (initialized after registration)
        self._stats_reporter: Optional[StatsReporter] = None

        # Rate limiter (per-IP connection cap; over-limit can auto-block)
        self._rate_limiter: Optional[RateLimiter] = None
        if getattr(settings, "rate_limit_per_minute", 0) > 0:
            on_rate_limit = None
            if getattr(settings, "rate_limit_auto_block", True):
                on_rate_limit = self._on_rate_limit_exceeded
            self._rate_limiter = RateLimiter(
                max_connections_per_minute=settings.rate_limit_per_minute,
                window_seconds=60,
                on_rate_limit=on_rate_limit,
            )

        # Optional GeoIP lookup (for geo allowlist/blocklist)
        self._geo_lookup: Optional[GeoLookup] = None
        if getattr(settings, "geolite2_db_path", None):
            self._geo_lookup = GeoLookup(settings.geolite2_db_path)
        else:
            self._geo_lookup = GeoLookup()

        # Proxy managers (TCP: scraper detection + rate limit + geo + idle timeout; UDP: same)
        listen_ip = settings.wireguard_ip if getattr(settings, "listen_on_wireguard_only", False) else settings.listen_ip
        self._tcp_manager = TCPProxyManager(
            on_connection=self._on_connection,
            scraper_ua_patterns=DEFAULT_SCRAPER_UA_PATTERNS,
            on_scraper_detected=self._on_scraper_detected,
            rate_limiter=self._rate_limiter,
            listen_ip=listen_ip,
            geo_lookup=self._geo_lookup,
        )
        self._udp_manager = UDPProxyManager(
            on_connection=self._on_connection,
            rate_limiter=self._rate_limiter,
            listen_ip=listen_ip,
            geo_lookup=self._geo_lookup,
        )

        # Firewall manager
        self._firewall_manager = FirewallManager()

        # Email proxy manager
        self._email_manager = EmailProxyManager()

        # Email stats collector (started after email proxy deployment)
        self._email_stats: Optional[EmailStatsCollector] = None

        # Security monitor for brute force detection
        self._security_monitor: Optional[SecurityMonitor] = None

        # iptables firewall stats monitor
        self._iptables_monitor: Optional[IptablesMonitor] = None

        # Controller communication
        self._heartbeat: Optional[HeartbeatSender] = None
        self._config_sync: Optional[ConfigSync] = None
        self._control_api: Optional[ControlAPI] = None

    def _on_connection(self, stats):
        """Called when a connection completes."""
        if self._stats_reporter:
            self._stats_reporter.record(stats)

    async def _on_auto_block(self, ip: str, reason: str, event_type: str):
        """Called by SecurityMonitor when an IP should be auto-blocked (aggressive firewall).
        Blocks locally (proxy + iptables) and reports to controller so blocklist syncs to all agents.
        """
        if not self.agent_id:
            return
        self._current_blocklist.add(ip)
        self._tcp_manager.update_blocklist(list(self._current_blocklist))
        self._udp_manager.update_blocklist(list(self._current_blocklist))
        await self._firewall_manager.add_blocklist_ip(ip)
        url = f"{settings.controller_url}/api/v1/blocklist/report"
        payload = {"ip": ip, "reason": reason, "agent_id": self.agent_id}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(url, json=payload)
                r.raise_for_status()
                logger.info(f"Auto-blocked {ip} ({event_type}) and reported to controller")
        except Exception as e:
            logger.warning(f"Failed to report block to controller for {ip}: {e}")

    async def _on_scraper_detected(self, ip: str):
        """Called when TCP proxy detects HTTP request with AI/scraper User-Agent. Block and report."""
        await self._on_auto_block(ip, "AI/scraper detected (User-Agent)", "scraper")

    async def _on_rate_limit_exceeded(self, ip: str, count: int):
        """Called when a client IP exceeds rate limit. Auto-block and report."""
        await self._on_auto_block(ip, f"Rate limit exceeded ({count} connections/min)", "rate_limit")

    def _get_active_connections(self) -> int:
        """Get total active connection count."""
        return self._tcp_manager.active_connections + self._udp_manager.active_connections

    async def _on_config_update(self, config: AgentConfig):
        """Handle configuration updates from controller."""
        logger.info(f"Applying config version {config.config_version}")

        # Controller is source of truth for blocklist (includes any IPs we reported via /report)
        self._current_blocklist = set(config.blocklist)

        # Update proxy blocklists
        self._tcp_manager.update_blocklist(list(self._current_blocklist))
        self._udp_manager.update_blocklist(list(self._current_blocklist))

        # Sync firewall blocklist chain (aggressive: drop by IP in iptables)
        await self._firewall_manager.sync_blocklist_ips(list(self._current_blocklist))

        # Convert services to dict format (no change to blocklist here)
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

        # Sync firewall rules (controller-defined port allow/block per interface)
        await self._firewall_manager.sync_rules(config.firewall_rules)

        # Geo filtering and idle timeout (from controller config)
        self._tcp_manager.set_geo(
            config.geo_mode or "off",
            config.geo_countries or [],
            self._geo_lookup,
        )
        self._tcp_manager.set_idle_timeout(getattr(config, "idle_connection_timeout_seconds", 0) or 0)
        self._udp_manager.set_geo(
            config.geo_mode or "off",
            config.geo_countries or [],
            self._geo_lookup,
        )
        self._udp_manager.set_idle_timeout(getattr(config, "idle_connection_timeout_seconds", 0) or 0)

        # Auto-generated baseline: public = default-deny (only proxy ports + established), WG = allow
        allowed_ports = [(s.listen_port, s.protocol.value) for s in config.services]
        await self._firewall_manager.apply_baseline(
            allowed_ports,
            harden_public=settings.harden_public_interface,
            allow_wg=settings.allow_wireguard_freedom,
        )

        # Apply email config if present and email proxy is deployed
        if config.email_config and self._email_manager.is_deployed:
            await self._email_manager.apply_config(config.email_config)

        logger.info(
            f"Config applied: {len([s for s in services if s['protocol'] == 'tcp'])} TCP services, "
            f"{len([s for s in services if s['protocol'] == 'udp'])} UDP services, "
            f"{len(self._current_blocklist)} blocked IPs, "
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
        result = await self._email_manager.deploy(hostname, mailcow_ip, mailcow_port, proxy_ip)
        success, message = result

        # Start email stats collector if deployment succeeded
        if success and self.agent_id and not self._email_stats:
            self._email_stats = EmailStatsCollector(self.agent_id)
            await self._email_stats.start()

        return result

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

    async def _check_email_proxy_deployed(self) -> bool:
        """Check if email proxy (Postfix) was previously deployed and is running."""
        try:
            # Check if Postfix is running
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", "postfix",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()

            if proc.returncode == 0 and stdout.decode().strip() == "active":
                # Mark email manager as deployed so config sync works
                self._email_manager._deployed = True
                return True
        except Exception as e:
            logger.debug(f"Could not check Postfix status: {e}")

        return False

    async def register(self) -> bool:
        """Register with the controller."""
        reg_data = {
            "hostname": settings.hostname,
            "wireguard_ip": settings.wireguard_ip,
            "public_ip": settings.public_ip,
            "version": settings.version,
        }
        if getattr(settings, "agent_secret", None):
            reg_data["agent_secret"] = settings.agent_secret

        url = f"{settings.controller_url}/api/v1/agents/register"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=reg_data)
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

        # Check if email proxy was previously deployed (Postfix running)
        # and start email stats collector if so
        if await self._check_email_proxy_deployed():
            self._email_stats = EmailStatsCollector(self.agent_id)
            await self._email_stats.start()
            logger.info("Email stats collector started (Postfix already deployed)")

        # Start security monitor (brute force, SSH, mail abuse); auto-blocks and reports to controller
        self._security_monitor = SecurityMonitor(
            self.agent_id,
            on_auto_block=self._on_auto_block,
        )
        await self._security_monitor.start()

        # Start iptables firewall stats monitor
        self._iptables_monitor = IptablesMonitor(self.agent_id)
        await self._iptables_monitor.start()

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

        if self._geo_lookup:
            self._geo_lookup.close()

        # Stop email proxy if deployed
        await self._email_manager.shutdown()

        # Stop email stats collector if running
        if self._email_stats:
            await self._email_stats.stop()

        # Stop iptables monitor
        if self._iptables_monitor:
            await self._iptables_monitor.stop()

        # Stop security monitor
        if self._security_monitor:
            await self._security_monitor.stop()

        if self._stats_reporter:
            await self._stats_reporter.stop()

        logger.info("NekoProxy agent stopped")


async def main(stop_event=None):
    """Main entry point. stop_event: optional threading.Event; when set, triggers agent shutdown (used by Windows service)."""
    agent = NekoProxyAgent()
    loop = asyncio.get_event_loop()

    # When running as Windows service, watch stop_event and call agent.stop() when set
    if stop_event is not None:
        def _watch_stop():
            stop_event.wait()
            loop.call_soon_threadsafe(lambda: asyncio.create_task(agent.stop()))

        import threading
        t = threading.Thread(target=_watch_stop, daemon=True)
        t.start()

    # Setup signal handlers (Unix only; Windows service uses stop_event)
    def signal_handler():
        logger.info("Received shutdown signal")
        asyncio.create_task(agent.stop())

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            pass

    try:
        await agent.start()
    except KeyboardInterrupt:
        await agent.stop()


def _agent_service_run(stop_event):
    """Run the agent until stop_event is set. Used by Windows service."""
    asyncio.run(main(stop_event=stop_event))


def _define_agent_service():
    if __name__ != "__main__":
        return None
    import sys
    if sys.platform != "win32":
        return None
    from shared.win_service import NekoProxyServiceFramework

    class AgentService(NekoProxyServiceFramework):
        _svc_name_ = "nekoproxy-agent"
        _svc_display_name_ = "NekoProxy Agent"
        _svc_description_ = "NekoProxy proxy agent (connects to controller)"
        _run_callback = _agent_service_run
    return AgentService


if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        # SCM runs the exe with arg "service" (argv = [exe_path, "service"]); we host the service
        if len(sys.argv) >= 2 and sys.argv[1].lower() == "service":
            import servicemanager
            AgentService = _define_agent_service()
            if AgentService is not None:
                servicemanager.PrepareToHostSingle(AgentService)
                AgentService(sys.argv).SvcRun()
                sys.exit(0)
        # User ran exe with install/start/stop/remove/debug
        cmd = sys.argv[1].lower() if len(sys.argv) > 1 else ""
        if cmd in ("install", "update", "start", "stop", "remove", "debug"):
            import win32serviceutil
            AgentService = _define_agent_service()
            if AgentService is not None:
                win32serviceutil.HandleCommandLine(AgentService, argv=sys.argv)
                sys.exit(0)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
