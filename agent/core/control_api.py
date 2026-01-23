"""Control API for receiving commands from controller."""

import asyncio
import logging
from typing import Callable, Optional, Any

try:
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except ImportError as e:
    AIOHTTP_AVAILABLE = False
    web = None

from agent.config import settings

logger = logging.getLogger(__name__)


class ControlAPI:
    """Simple HTTP API for receiving control commands from controller."""

    def __init__(
        self,
        trigger_sync: Callable[[], asyncio.Future],
        deploy_email: Optional[Callable[[str, int, str], asyncio.Future]] = None,
        trigger_email_sync: Optional[Callable[[], asyncio.Future]] = None
    ):
        """
        Initialize control API.

        Args:
            trigger_sync: Callback to trigger immediate config sync
            deploy_email: Callback to deploy email proxy (mailcow_host, mailcow_port, proxy_ip)
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

            # Email proxy endpoints
            self._app.router.add_post("/deploy-email", self._handle_deploy_email)
            self._app.router.add_post("/trigger-email-sync", self._handle_trigger_email_sync)

            self._runner = web.AppRunner(self._app)
            await self._runner.setup()

            # Listen on wireguard IP only (not public interface)
            self._site = web.TCPSite(
                self._runner,
                settings.wireguard_ip,
                settings.api_port
            )
            await self._site.start()

            logger.info(f"Control API listening on {settings.wireguard_ip}:{settings.api_port}")
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

    async def _handle_deploy_email(self, request: web.Request) -> web.Response:
        """Handle email proxy deployment request from controller."""
        if not self.deploy_email:
            return web.json_response(
                {"status": "error", "message": "Email deployment not supported"},
                status=501
            )

        logger.info("Received email proxy deployment request")
        try:
            data = await request.json()
            mailcow_host = data.get("mailcow_host")
            mailcow_port = data.get("mailcow_port", 25)
            proxy_ip = data.get("proxy_ip")

            if not mailcow_host or not proxy_ip:
                return web.json_response(
                    {"status": "error", "message": "Missing required fields: mailcow_host, proxy_ip"},
                    status=400
                )

            result = await self.deploy_email(mailcow_host, mailcow_port, proxy_ip)
            # Handle both tuple (success, error_msg) and bool return values
            if isinstance(result, tuple):
                success, error_msg = result
            else:
                success, error_msg = result, None

            if success:
                return web.json_response({"status": "ok", "message": "Email proxy deployed"})
            else:
                return web.json_response(
                    {"status": "error", "message": error_msg or "Deployment failed"},
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
