"""Heartbeat sender for agent health reporting."""

import asyncio
import logging
import psutil
from typing import Callable, Optional

import httpx

from agent.config import settings

logger = logging.getLogger(__name__)


class HeartbeatSender:
    """Sends periodic heartbeats to the controller."""

    def __init__(
        self,
        agent_id: int,
        get_active_connections: Callable[[], int]
    ):
        self.agent_id = agent_id
        self.get_active_connections = get_active_connections
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None

    async def start(self):
        """Start the heartbeat loop."""
        self._running = True
        self._client = httpx.AsyncClient(timeout=10.0)
        self._task = asyncio.create_task(self._heartbeat_loop())
        logger.info(f"Heartbeat sender started (interval: {settings.heartbeat_interval}s)")

    async def stop(self):
        """Stop the heartbeat loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
        logger.info("Heartbeat sender stopped")

    async def _heartbeat_loop(self):
        """Main heartbeat loop."""
        while self._running:
            try:
                await self._send_heartbeat()
            except Exception as e:
                logger.error(f"Heartbeat failed: {e}")

            await asyncio.sleep(settings.heartbeat_interval)

    async def _send_heartbeat(self):
        """Send a single heartbeat."""
        # Gather system metrics
        cpu_percent = psutil.cpu_percent(interval=None)
        memory_percent = psutil.virtual_memory().percent
        active_connections = self.get_active_connections()

        heartbeat_data = {
            "active_connections": active_connections,
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "bytes_sent": 0,  # Could track cumulative if needed
            "bytes_received": 0
        }

        url = f"{settings.controller_url}/api/v1/agents/{self.agent_id}/heartbeat"

        try:
            response = await self._client.post(url, json=heartbeat_data)
            response.raise_for_status()
            logger.debug(f"Heartbeat sent: {active_connections} connections")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.error("Agent not found - may need to re-register")
            raise
        except httpx.RequestError as e:
            logger.warning(f"Controller unreachable: {e}")
            raise
