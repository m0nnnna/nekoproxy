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
from agent.core.firewall_windows import WindowsFirewallManager
from agent.core.control_api import ControlAPI
from agent.core.email_proxy import EmailProxyManager
from agent.core.forward_proxy import ForwardProxyServer
from agent.core.dns_forwarder import DnsForwarder
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
        listen_ip = (settings.wireguard_ip if getattr(settings, "listen_on_wireguard_only", False) and settings.wireguard_ip else settings.listen_ip)
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

        # Firewall manager (Linux iptables or Windows Firewall)
        self._firewall_manager = WindowsFirewallManager() if sys.platform == "win32" else FirewallManager()

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

        # Forward proxy server (optional; disabled when forward_proxy_port == 0)
        self._forward_proxy: Optional[ForwardProxyServer] = None
        self._forward_proxy_task: Optional[asyncio.Task] = None

        # DNS forwarder (optional; disabled when dns_port == 0)
        self._dns_forwarder: Optional[DnsForwarder] = None
        self._dns_forwarder_task: Optional[asyncio.Task] = None

    def _on_connection(self, stats):
        """Called when a connection completes."""
        if self._stats_reporter:
            self._stats_reporter.record(stats)

    def _make_agent_headers(self) -> dict:
        """Return auth headers for agent→controller HTTP requests."""
        if settings.agent_token:
            return {"X-Agent-Token": settings.agent_token}
        return {}

    def _make_httpx_client(self, timeout: float = 10.0) -> httpx.AsyncClient:
        """Return an httpx client configured for the controller (SSL verify + token)."""
        ssl_verify = settings.controller_ssl_ca_cert or settings.controller_ssl_verify or False
        return httpx.AsyncClient(timeout=timeout, verify=ssl_verify)

    async def _on_auto_block(self, ip: str, reason: str, event_type: str):
        """Called by SecurityMonitor when an IP should be auto-blocked (aggressive firewall).
        Blocks locally (proxy + firewall) and reports to controller so blocklist syncs to all agents.
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
            async with self._make_httpx_client() as client:
                r = await client.post(url, json=payload, headers=self._make_agent_headers())
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
        try:
            await self._apply_config(config)
        except Exception as e:
            logger.error("Config update failed at version %s: %s", config.config_version, e, exc_info=True)

    async def _apply_config(self, config: AgentConfig):
        logger.info(f"Applying config version {config.config_version}")

        # Controller is source of truth for blocklist (includes any IPs we reported via /report)
        self._current_blocklist = set(config.blocklist)

        # Update proxy blocklists
        self._tcp_manager.update_blocklist(list(self._current_blocklist))
        self._udp_manager.update_blocklist(list(self._current_blocklist))

        # Sync firewall blocklist (iptables on Linux, Windows Firewall on Windows)
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

        # Sync firewall rules (controller-defined port allow/block)
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

        # Auto-generated baseline: public = restrictive (only proxy ports), private/WG = permissive
        allowed_ports = [(s.listen_port, s.protocol.value) for s in config.services]
        await self._firewall_manager.apply_baseline(
            allowed_ports,
            harden_public=settings.harden_public_interface,
            allow_wg=settings.allow_wireguard_freedom,
            allow_dangerous_ports_on_public=getattr(config, "internal", False),
        )

        # Apply email config if present and email proxy is deployed
        if config.email_config and self._email_manager.is_deployed:
            await self._email_manager.apply_config(config.email_config)

        # Upstream proxy: apply routing-via-agent setting from controller
        new_upstream = getattr(config, "upstream_proxy", None)
        if new_upstream != settings.upstream_proxy:
            settings.upstream_proxy = new_upstream
            if new_upstream:
                logger.info("Upstream proxy set to %s (route-via from controller)", new_upstream)
            else:
                logger.info("Upstream proxy cleared (direct connections)")

        # Forward proxy: start/stop/restart based on controller config
        await self._apply_forward_proxy_config(
            getattr(config, "forward_proxy_port", 0) or 0,
            getattr(config, "forward_proxy_auth", None),
        )

        # DNS forwarder: start/stop/restart based on controller config
        await self._apply_dns_config(
            getattr(config, "dns_port", 0) or 0,
            getattr(config, "dns_upstream", "1.1.1.1:53") or "1.1.1.1:53",
        )

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

    async def _apply_forward_proxy_config(self, port: int, auth: Optional[str]):
        """Start, stop, or restart the forward proxy based on the controller config."""
        current_port = self._forward_proxy._port if self._forward_proxy else 0
        current_auth = self._forward_proxy._auth if self._forward_proxy else None
        current_upstream = self._forward_proxy._upstream if self._forward_proxy else None
        # Normalize auth for comparison
        new_auth_tuple = None
        if auth and ":" in auth:
            u, p = auth.split(":", 1)
            new_auth_tuple = (u.strip(), p.strip())

        # The upstream/exit (route-via) is captured at construction and used for
        # every backend connection. If the controller changes it but port/auth
        # stay the same, we MUST still restart — otherwise the running proxy keeps
        # dialing the old exit (or direct), so an internal agent with no direct
        # egress listens but forwards nothing.
        new_upstream = getattr(settings, "upstream_proxy", None)
        upstream_changed = new_upstream != current_upstream

        # Also restart if the task silently died (bind error, etc.)
        task_dead = self._forward_proxy_task is not None and self._forward_proxy_task.done()
        if task_dead and self._forward_proxy_task.exception() is not None:
            logger.warning("Forward proxy task had crashed (%s) — restarting", self._forward_proxy_task.exception())

        if port == current_port and new_auth_tuple == current_auth and not upstream_changed and not task_dead:
            return  # No change
        if upstream_changed and self._forward_proxy:
            logger.info("Forward proxy upstream changed (%s → %s) — restarting", current_upstream, new_upstream)

        # Stop existing forward proxy if running
        if self._forward_proxy:
            await self._forward_proxy.stop()
            if self._forward_proxy_task and not self._forward_proxy_task.done():
                self._forward_proxy_task.cancel()
                try:
                    await self._forward_proxy_task
                except asyncio.CancelledError:
                    pass
            self._forward_proxy = None
            self._forward_proxy_task = None

        if port > 0:
            # Bind to WireGuard IP when available so the forward proxy is never
            # reachable on the public internet interface at the socket level.
            # Internal agents (no WireGuard) fall back to listen_ip.
            listen_ip = settings.wireguard_ip or settings.listen_ip
            self._forward_proxy = ForwardProxyServer(
                listen_ip=listen_ip,
                port=port,
                upstream_proxy=getattr(settings, "upstream_proxy", None),
                auth=auth,
                on_connection=self._on_connection,
                max_connections=getattr(settings, "forward_proxy_max_connections", 500),
                idle_timeout=getattr(settings, "forward_proxy_idle_timeout", 180),
                overflow_wait=getattr(settings, "forward_proxy_overflow_wait", 5),
                max_per_host=getattr(settings, "forward_proxy_max_per_host", 32),
            )
            self._forward_proxy_task = asyncio.create_task(self._run_forward_proxy(listen_ip, port))

    async def _run_forward_proxy(self, listen_ip: str, port: int):
        """Run the forward proxy server and log any startup/runtime errors."""
        try:
            await self._forward_proxy.start()
        except OSError as e:
            logger.error(
                "Forward proxy failed to bind on %s:%d — %s  "
                "(check that the WireGuard interface is up and the IP is assigned)",
                listen_ip, port, e,
            )
        except Exception as e:
            logger.error("Forward proxy on %s:%d crashed: %s", listen_ip, port, e)

    async def _apply_dns_config(self, port: int, upstream: str):
        """Start, stop, or restart the DNS forwarder based on the controller config."""
        current_port = self._dns_forwarder._port if self._dns_forwarder else 0
        current_upstream = self._dns_forwarder._upstream if self._dns_forwarder else None

        task_dead = self._dns_forwarder_task is not None and self._dns_forwarder_task.done()
        if task_dead and self._dns_forwarder_task.exception() is not None:
            logger.warning("DNS forwarder task had crashed (%s) — restarting", self._dns_forwarder_task.exception())

        if port == current_port and upstream == current_upstream and not task_dead:
            return

        if self._dns_forwarder:
            await self._dns_forwarder.stop()
            if self._dns_forwarder_task and not self._dns_forwarder_task.done():
                self._dns_forwarder_task.cancel()
                try:
                    await self._dns_forwarder_task
                except asyncio.CancelledError:
                    pass
            self._dns_forwarder = None
            self._dns_forwarder_task = None

        if port > 0:
            # Bind to WireGuard IP when available so the DNS forwarder is never
            # reachable on the public internet interface at the socket level.
            # Internal agents (no WireGuard) fall back to listen_ip.
            listen_ip = settings.wireguard_ip or settings.listen_ip
            self._dns_forwarder = DnsForwarder(
                listen_ip=listen_ip,
                port=port,
                upstream=upstream,
                on_connection=self._on_connection,
            )
            self._dns_forwarder_task = asyncio.create_task(self._run_dns_forwarder(listen_ip, port, upstream))

    async def _run_dns_forwarder(self, listen_ip: str, port: int, upstream: str):
        """Run the DNS forwarder and log any startup/runtime errors."""
        try:
            await self._dns_forwarder.start()
        except OSError as e:
            logger.error(
                "DNS forwarder failed to bind on %s:%d — %s  "
                "(check that the WireGuard interface is up and the IP is assigned)",
                listen_ip, port, e,
            )
        except Exception as e:
            logger.error("DNS forwarder on %s:%d crashed: %s", listen_ip, port, e)

    async def _on_auth_failed(self):
        """Re-register with controller to refresh agent token after a 401 rejection."""
        logger.warning("Token rejected by controller — re-registering to refresh token")
        success = await self.register()
        if success:
            logger.info("Re-registration successful — new token obtained")
        else:
            logger.error("Re-registration failed; will retry on next heartbeat/sync")

    async def _on_regen_cert(self):
        """Rebuild httpx clients in heartbeat and config_sync after cert re-TOFU."""
        if self._heartbeat:
            await self._heartbeat._rebuild_client()
        if self._config_sync:
            await self._config_sync._rebuild_client()

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

    async def _tofu_download_controller_cert(self) -> None:
        """Download and cache the controller's TLS certificate (Trust On First Use).

        Called once after first successful registration when the controller is
        running over HTTPS with an auto-generated (or any self-signed) cert.
        After this, future connections can verify against the cached cert.
        """
        if settings.controller_ssl_ca_cert:
            return  # Already have a CA cert configured
        if not settings.controller_url.startswith("https://"):
            return  # HTTP — nothing to do

        cert_save_path = settings.install_dir_resolved / "controller-ca.pem"
        if cert_save_path.is_file():
            # Already cached from a previous run — load it
            settings.controller_ssl_ca_cert = str(cert_save_path)
            logger.info("Loaded cached controller cert: %s", cert_save_path)
            return

        url = f"{settings.controller_url}/api/v1/agents/controller-cert"
        try:
            # First download uses verify=False (TOFU — same model as SSH host keys).
            # After this the cert is pinned and all future connections verify against it.
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                r = await client.get(url)
                r.raise_for_status()
                cert_save_path.write_text(r.text, encoding="utf-8")
                settings.controller_ssl_ca_cert = str(cert_save_path)

                from shared.tls import cert_fingerprint
                fp = cert_fingerprint(cert_save_path)
                logger.warning("=" * 70)
                logger.warning("CONTROLLER CERTIFICATE CACHED (Trust On First Use)")
                logger.warning("  Saved to: %s", cert_save_path)
                logger.warning("  SHA-256 fingerprint: %s", fp)
                logger.warning("  Verify this matches the fingerprint shown on the controller.")
                logger.warning("  Future connections will be verified against this cert.")
                logger.warning("=" * 70)
        except Exception as e:
            logger.warning("Could not download controller cert for TOFU: %s", e)

    async def register(self) -> bool:
        """Register with the controller."""
        reg_data = {
            "hostname": settings.hostname,
            "public_ip": settings.public_ip,
            "version": settings.version,
        }
        if settings.wireguard_ip:
            reg_data["wireguard_ip"] = settings.wireguard_ip
        else:
            reg_data["wireguard_ip"] = None  # Internal agent (no WireGuard)
        if getattr(settings, "control_url", None) and (settings.control_url or "").strip():
            reg_data["control_url"] = (settings.control_url or "").strip().rstrip("/")
        if getattr(settings, "agent_secret", None):
            reg_data["agent_secret"] = settings.agent_secret

        url = f"{settings.controller_url}/api/v1/agents/register"

        # Send existing token so the controller can recognise this as a known agent
        # re-registering and bypass the agent_secret check when the secret changed.
        reg_headers = {}
        if settings.agent_token:
            reg_headers["X-Agent-Token"] = settings.agent_token

        try:
            # On first run the controller cert may not be cached yet — use verify=False
            # for registration itself, then immediately download and cache the cert.
            ssl_verify = settings.controller_ssl_ca_cert or settings.controller_ssl_verify or False
            async with httpx.AsyncClient(timeout=10.0, verify=ssl_verify) as client:
                response = await client.post(url, json=reg_data, headers=reg_headers)
                response.raise_for_status()
                data = response.json()
                self.agent_id = data["id"]

                # Save per-agent token returned by controller
                token = data.get("agent_token")
                if token:
                    settings.agent_token = token
                    try:
                        token_file = settings.install_dir_resolved / "agent_token.txt"
                        token_file.write_text(token, encoding="utf-8")
                        logger.info(f"Agent token saved to {token_file}")
                    except Exception as e:
                        logger.warning(f"Could not save agent token to file: {e}")

                logger.info(
                    f"Registered with controller as agent {self.agent_id} "
                    f"({settings.hostname} @ {settings.wireguard_ip})"
                )

                # TOFU: download and cache the controller cert so future connections
                # can be properly verified (skipped if cert already cached).
                await self._tofu_download_controller_cert()

                return True
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to controller: {e}")
            return False
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                logger.error(
                    "Registration failed: 401 Unauthorized. "
                    "Check that NEKO_AGENT_AGENT_SECRET in agent.env matches "
                    "NEKO_AGENT_SECRET on the controller."
                )
            else:
                logger.error(f"Registration failed: HTTP {e.response.status_code} - {e.response.text}")
            return False

    def _pre_generate_control_cert(self):
        """Pre-generate ControlAPI TLS cert before registration.

        Generating the cert first lets us:
        1. Write the cert files to /opt/nekoproxy (or exe dir) before anything else.
        2. Auto-set settings.control_url to https://... so the controller knows our scheme.
        This must be called synchronously (no await) before register().
        """
        try:
            from shared.tls import ensure_cert
            install_dir = settings.install_dir_resolved
            install_dir.mkdir(parents=True, exist_ok=True)
            cert_path = install_dir / "agent-cert.pem"
            key_path = install_dir / "agent-key.pem"
            extra_ips = [ip for ip in [settings.wireguard_ip, settings.public_ip] if ip]
            generated, fp = ensure_cert(cert_path, key_path, extra_ips=extra_ips)
            settings.control_ssl_certfile = str(cert_path)
            settings.control_ssl_keyfile = str(key_path)
            if generated:
                logger.info("Generated ControlAPI TLS cert (SHA-256: %s) at %s", fp, cert_path)
            else:
                logger.info("Using existing ControlAPI TLS cert (SHA-256: %s)", fp)

            # Auto-set control_url to https so the controller uses TLS when calling us back.
            # Only set if not explicitly configured by the user.
            if not settings.control_url:
                bind_ip = (
                    getattr(settings, "control_bind_ip", None)
                    or settings.wireguard_ip
                    or "127.0.0.1"
                )
                settings.control_url = f"https://{bind_ip}:{settings.api_port}"
                logger.info("Auto-set control_url: %s", settings.control_url)
        except Exception as e:
            logger.error(
                "Could not generate ControlAPI TLS cert: %s  "
                "ControlAPI will run on HTTP (unencrypted). "
                "Ensure 'cryptography' is installed / bundled.",
                e,
            )

    @staticmethod
    def _raise_fd_limit():
        """Raise the open-file-descriptor soft limit to the hard limit (Linux/Unix).

        Each proxied connection uses 2 FDs (client + backend). Federating servers
        (Misskey/Matrix) burst hundreds of outbound connections, and the default
        soft limit (often 1024) is hit fast → 'Too many open files', which stalls
        accept() and crashes the proxy. No-op on Windows (no resource module).
        """
        try:
            import resource
        except ImportError:
            return  # Windows
        try:
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            if soft < hard:
                resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
                logger.info("Raised open-file limit: %s → %s", soft, hard)
        except Exception as e:
            logger.warning("Could not raise open-file (RLIMIT_NOFILE) limit: %s", e)

    async def start(self):
        """Start the NekoProxy agent."""
        self._raise_fd_limit()
        logger.info("=" * 70)
        logger.info("NekoProxy Agent Starting")
        logger.info(f"  Hostname: {settings.hostname}")
        logger.info(f"  WireGuard IP: {settings.wireguard_ip or '(internal - none)'}")
        logger.info(f"  Controller: {settings.controller_url}")
        # Surface which config file(s) actually exist so a "hidden" config (the
        # installer writes /etc/nekoproxy/agent.env) is never a mystery again.
        try:
            from agent.config import _agent_env_files
            from os.path import isfile
            found = [f for f in _agent_env_files() if isfile(f)]
            logger.info(f"  Config file(s): {', '.join(found) if found else '(none found — using env vars / defaults)'}")
            if settings.controller_url == "http://localhost:8001" and not found:
                logger.warning(
                    "  Controller URL is the built-in default and no config file was found. "
                    "If run by hand, note systemd loads /etc/nekoproxy/agent.env via EnvironmentFile."
                )
        except Exception:
            pass
        logger.info("=" * 70)

        # Generate ControlAPI TLS cert BEFORE registering so the controller
        # receives our https:// control_url during registration.
        self._pre_generate_control_cert()

        self._running = True

        # Register with controller. Retry instead of exiting: at boot (Windows
        # service / systemd) the controller or the network may not be ready yet,
        # and giving up would stop the service until someone restarts it by hand.
        attempt = 0
        while self._running:
            if await self.register():
                break
            attempt += 1
            delay = min(60, 5 * attempt)
            logger.warning(
                "Registration failed (attempt %d) - retrying in %ds", attempt, delay
            )
            for _ in range(delay):
                if not self._running:
                    break
                await asyncio.sleep(1)
        if not self._running:
            logger.info("Stop requested before registration completed")
            return

        # Initialize firewall manager (iptables on Linux, Windows Firewall on Windows; requires admin on Windows)
        await self._firewall_manager.initialize()

        # Initialize stats reporter
        self._stats_reporter = StatsReporter(self.agent_id)
        await self._stats_reporter.start()

        # Start heartbeat
        self._heartbeat = HeartbeatSender(
            agent_id=self.agent_id,
            get_active_connections=self._get_active_connections,
            on_auth_failed=self._on_auth_failed,
        )
        await self._heartbeat.start()

        # Start config sync (will apply initial config)
        self._config_sync = ConfigSync(
            agent_id=self.agent_id,
            on_config_update=lambda c: asyncio.create_task(self._on_config_update(c)),
            on_auth_failed=self._on_auth_failed,
        )
        await self._config_sync.start()

        # Start control API (for receiving push notifications)
        self._control_api = ControlAPI(
            trigger_sync=self._config_sync.force_sync,
            deploy_email=self._deploy_email,
            trigger_email_sync=self._trigger_email_sync,
            on_regen_cert=self._on_regen_cert,
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

        # Start iptables firewall stats monitor (Linux only)
        if sys.platform != "win32":
            self._iptables_monitor = IptablesMonitor(self.agent_id)
            await self._iptables_monitor.start()

        # Start forward proxy if configured via env (controller config handled by _apply_forward_proxy_config)
        if getattr(settings, "forward_proxy_port", 0):
            _fwd_listen_ip = settings.wireguard_ip or settings.listen_ip
            self._forward_proxy = ForwardProxyServer(
                listen_ip=_fwd_listen_ip,
                port=settings.forward_proxy_port,
                upstream_proxy=getattr(settings, "upstream_proxy", None),
                auth=getattr(settings, "forward_proxy_auth", None),
                on_connection=self._on_connection,
                max_connections=getattr(settings, "forward_proxy_max_connections", 500),
                idle_timeout=getattr(settings, "forward_proxy_idle_timeout", 180),
                overflow_wait=getattr(settings, "forward_proxy_overflow_wait", 5),
                max_per_host=getattr(settings, "forward_proxy_max_per_host", 32),
            )
            self._forward_proxy_task = asyncio.create_task(self._forward_proxy.start())

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

        if self._forward_proxy:
            await self._forward_proxy.stop()
        if self._forward_proxy_task:
            self._forward_proxy_task.cancel()
            try:
                await self._forward_proxy_task
            except asyncio.CancelledError:
                pass

        if self._dns_forwarder:
            await self._dns_forwarder.stop()
        if self._dns_forwarder_task:
            self._dns_forwarder_task.cancel()
            try:
                await self._dns_forwarder_task
            except asyncio.CancelledError:
                pass

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
    """Run the agent until stop_event is set. Used by Windows service.

    Runs with cwd = C:\\Windows\\System32, so logging must be redirected to a file
    next to the exe or a failed start leaves no trace.
    """
    from shared.win_service import setup_service_logging
    log_path = setup_service_logging("nekoproxy-agent")
    logger.info("NekoProxy Agent service starting (log: %s)", log_path)
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
        _svc_description_ = "NekoProxy proxy agent (connects to controller). Starts automatically at boot."
        _run_callback = staticmethod(_agent_service_run)
    return AgentService


if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        cmd = sys.argv[1].lower() if len(sys.argv) > 1 else ""
        # The SCM launches the exe as "<exe> service". Hosting a frozen service:
        # Initialize -> PrepareToHostSingle -> StartServiceCtrlDispatcher. The
        # dispatcher call is what connects this process to the SCM; without it the
        # service dies in __init__ before anything runs (Start-Service -> 1053).
        if cmd == "service":
            import servicemanager
            AgentService = _define_agent_service()
            if AgentService is not None:
                try:
                    servicemanager.Initialize()
                    servicemanager.PrepareToHostSingle(AgentService)
                    servicemanager.StartServiceCtrlDispatcher()
                except SystemExit:
                    raise
                except BaseException as e:
                    # 1063 = not started by the SCM (someone ran "<exe> service" by hand)
                    if getattr(e, "winerror", None) == 1063:
                        print("This command is for the Service Control Manager. "
                              "Use install-agent.ps1 (or '<exe> install' then 'start').")
                        sys.exit(1)
                    raise
                sys.exit(0)
        # User ran exe with install/start/stop/remove/debug
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
