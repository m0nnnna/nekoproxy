"""Control API for receiving commands from controller.

All endpoints require a valid X-Controller-Token header when
settings.controller_token is configured.  This prevents unauthorized
parties from triggering syncs, pushing binaries, or deploying email
proxies without knowing the shared controller token.
"""

import asyncio
import hmac
import logging
import os
import ssl
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional, Any

try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    web = None

from agent.config import settings

logger = logging.getLogger(__name__)


def _check_controller_token(request) -> bool:
    """Return True if the request carries a valid controller token (or no token is required)."""
    required = settings.controller_token
    if not required:
        return True  # No token configured — allow (backward compat / first run)
    provided = request.headers.get("X-Controller-Token", "")
    return bool(provided) and hmac.compare_digest(provided, required)


def _token_error() -> web.Response:
    return web.json_response(
        {"status": "error", "message": "Invalid or missing controller token"},
        status=401,
    )


class ControlAPI:
    """Simple HTTP API for receiving control commands from controller."""

    def __init__(
        self,
        trigger_sync: Callable[[], asyncio.Future],
        deploy_email: Optional[Callable[[str, str, int, str], asyncio.Future]] = None,
        trigger_email_sync: Optional[Callable[[], asyncio.Future]] = None
    ):
        """
        Initialize control API.

        Args:
            trigger_sync: Callback to trigger immediate config sync
            deploy_email: Callback to deploy email proxy (hostname, mailcow_ip, mailcow_port, proxy_ip)
            trigger_email_sync: Callback to trigger email config sync
        """
        self.trigger_sync = trigger_sync
        self.deploy_email = deploy_email
        self.trigger_email_sync = trigger_email_sync
        self._app = None
        self._runner = None
        self._site = None

    async def start(self):
        """Start the control API server."""
        if not AIOHTTP_AVAILABLE:
            logger.warning("aiohttp not available - Control API disabled (push sync won't work)")
            return

        try:
            self._app = web.Application()
            self._app.router.add_post("/trigger-sync", self._handle_trigger_sync)
            self._app.router.add_get("/health", self._handle_health)
            self._app.router.add_post("/update-binary", self._handle_update_binary)

            # Email proxy endpoints
            self._app.router.add_post("/deploy-email", self._handle_deploy_email)
            self._app.router.add_post("/trigger-email-sync", self._handle_trigger_email_sync)

            self._runner = web.AppRunner(self._app)
            await self._runner.setup()

            # ── TLS: auto-generate cert if none configured ────────────────
            ssl_ctx = None
            certfile = getattr(settings, "control_ssl_certfile", None)
            keyfile = getattr(settings, "control_ssl_keyfile", None)

            if not certfile:
                # Auto-generate a self-signed cert for the ControlAPI so that
                # all controller → agent traffic is encrypted.
                try:
                    from shared.tls import ensure_cert
                    install_dir = settings.install_dir_resolved
                    cert_path = install_dir / "agent-cert.pem"
                    key_path  = install_dir / "agent-key.pem"
                    extra_ips = [
                        ip for ip in [
                            getattr(settings, "wireguard_ip", None),
                            getattr(settings, "public_ip", None),
                        ] if ip
                    ]
                    generated, fp = ensure_cert(cert_path, key_path, extra_ips=extra_ips)
                    settings.control_ssl_certfile = str(cert_path)
                    settings.control_ssl_keyfile  = str(key_path)
                    certfile = str(cert_path)
                    keyfile  = str(key_path)
                    if generated:
                        logger.info("Auto-generated ControlAPI TLS cert (SHA-256: %s)", fp)
                    else:
                        logger.info("Using existing ControlAPI TLS cert (SHA-256: %s)", fp)
                except Exception as e:
                    logger.warning("Could not auto-generate ControlAPI TLS cert: %s", e)

            if certfile and keyfile:
                ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ssl_ctx.load_cert_chain(certfile, keyfile)
                logger.info(f"Control API TLS enabled (cert: {certfile})")

            # Bind ControlAPI to the management network only — never the public internet interface.
            # Priority: explicit control_bind_ip > wireguard_ip > loopback (127.0.0.1).
            # Using 0.0.0.0 would expose port 8002 on all interfaces including the public IP.
            if getattr(settings, "control_bind_ip", None):
                bind_host = settings.control_bind_ip
            elif settings.wireguard_ip:
                bind_host = settings.wireguard_ip
            else:
                bind_host = "127.0.0.1"
            self._site = web.TCPSite(
                self._runner,
                bind_host,
                settings.api_port,
                ssl_context=ssl_ctx,
            )
            await self._site.start()

            scheme = "https" if ssl_ctx else "http"
            logger.info(f"Control API listening on {scheme}://{bind_host}:{settings.api_port}")
        except Exception as e:
            logger.error(f"Failed to start Control API: {e}")
            logger.warning("Push sync from controller will not work")

    async def stop(self):
        """Stop the control API server."""
        if self._runner:
            await self._runner.cleanup()
        logger.info("Control API stopped")

    async def _handle_trigger_sync(self, request: web.Request) -> web.Response:
        """Handle sync trigger from controller."""
        if not _check_controller_token(request):
            return _token_error()
        logger.info("Received sync trigger from controller")
        try:
            await self.trigger_sync()
            return web.json_response({"status": "ok", "message": "Sync triggered"})
        except Exception as e:
            logger.error(f"Error triggering sync: {e}")
            return web.json_response(
                {"status": "error", "message": str(e)},
                status=500
            )

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({"status": "healthy"})

    async def _handle_update_binary(self, request: web.Request) -> web.Response:
        """Receive new agent binary from controller, write to staging path, then spawn update script (agent will restart)."""
        if not _check_controller_token(request):
            return _token_error()
        install_dir = settings.install_dir_resolved
        is_windows = sys.platform == "win32"
        staging_name = "nekoproxy-agent.new.exe" if is_windows else "nekoproxy-agent.new"
        staging_path = install_dir / staging_name

        try:
            body = await request.read()
            if not body:
                return web.json_response({"status": "error", "message": "Empty body"}, status=400)
            install_dir.mkdir(parents=True, exist_ok=True)
            staging_path.write_bytes(body)
            if not is_windows:
                os.chmod(staging_path, 0o755)
        except Exception as e:
            logger.exception("Failed to write update binary")
            return web.json_response({"status": "error", "message": str(e)}, status=500)

        # Spawn detached process that will stop agent, replace binary, restart (after a short delay)
        try:
            if is_windows:
                current_exe = getattr(sys, "executable", None) or str(staging_path)
                ps = (
                    f"Start-Sleep -Seconds 3; "
                    f"Stop-Service -Name 'nekoproxy-agent' -Force -ErrorAction SilentlyContinue; "
                    f"Start-Sleep -Seconds 2; "
                    f"Copy-Item -Path '{staging_path}' -Destination '{current_exe}' -Force; "
                    f"Start-Service -Name 'nekoproxy-agent'"
                )
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
                subprocess.Popen(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                    creationflags=flags,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=str(install_dir),
                )
            else:
                update_script = install_dir / "update-agent.sh"
                if not update_script.is_file():
                    return web.json_response(
                        {"status": "error", "message": f"update-agent.sh not found in {install_dir}"},
                        status=501
                    )
                cmd = ["/bin/sh", "-c", f"sleep 3; {update_script} {staging_path}"]
                subprocess.Popen(
                    cmd,
                    start_new_session=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=str(install_dir),
                )
        except Exception as e:
            logger.exception("Failed to spawn update process")
            return web.json_response({"status": "error", "message": str(e)}, status=500)

        logger.info("Update binary saved; restart scheduled")
        return web.json_response({"status": "ok", "message": "Update received; agent will restart shortly"})

    async def _handle_deploy_email(self, request: web.Request) -> web.Response:
        """Handle email proxy deployment request from controller."""
        if not _check_controller_token(request):
            return _token_error()
        if not self.deploy_email:
            return web.json_response(
                {"status": "error", "message": "Email deployment not supported"},
                status=501
            )

        logger.info("Received email proxy deployment request")
        try:
            data = await request.json()
            hostname = data.get("hostname")
            mailcow_ip = data.get("mailcow_ip")
            mailcow_port = data.get("mailcow_port", 25)
            proxy_ip = data.get("proxy_ip")

            if not hostname or not mailcow_ip or not proxy_ip:
                return web.json_response(
                    {"status": "error", "message": "Missing required fields: hostname, mailcow_ip, proxy_ip"},
                    status=400
                )

            result = await self.deploy_email(hostname, mailcow_ip, mailcow_port, proxy_ip)
            # Handle both tuple (success, message) and bool return values
            if isinstance(result, tuple):
                success, message = result
            else:
                success, message = result, None

            if success:
                response = {"status": "ok", "message": "Email proxy deployed"}
                # Include SSL warning if present
                if message:
                    response["warning"] = message
                return web.json_response(response)
            else:
                return web.json_response(
                    {"status": "error", "message": message or "Deployment failed"},
                    status=500
                )
        except Exception as e:
            logger.error(f"Error deploying email proxy: {e}")
            return web.json_response(
                {"status": "error", "message": str(e)},
                status=500
            )

    async def _handle_trigger_email_sync(self, request: web.Request) -> web.Response:
        """Handle email config sync trigger from controller."""
        if not _check_controller_token(request):
            return _token_error()
        if not self.trigger_email_sync:
            return web.json_response(
                {"status": "error", "message": "Email sync not supported"},
                status=501
            )

        logger.info("Received email sync trigger from controller")
        try:
            await self.trigger_email_sync()
            return web.json_response({"status": "ok", "message": "Email sync triggered"})
        except Exception as e:
            logger.error(f"Error triggering email sync: {e}")
            return web.json_response(
                {"status": "error", "message": str(e)},
                status=500
            )
