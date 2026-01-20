"""Control API for receiving commands from controller."""

import asyncio
import logging
from typing import Callable, Optional

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

    def __init__(self, trigger_sync: Callable[[], asyncio.Future]):
        """
        Initialize control API.

        Args:
            trigger_sync: Callback to trigger immediate config sync
        """
        self.trigger_sync = trigger_sync
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
