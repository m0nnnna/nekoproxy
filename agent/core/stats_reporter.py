"""Stats reporter for sending connection statistics to controller."""

import asyncio
import logging
from datetime import datetime
from typing import Optional
from collections import deque

import httpx

from agent.config import settings

logger = logging.getLogger(__name__)


class StatsReporter:
    """Batches and reports connection statistics to controller."""

    def __init__(self, agent_id: int):
        self.agent_id = agent_id
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None
        self._stats_queue: deque = deque(maxlen=10000)  # Prevent unbounded growth

    async def start(self):
        """Start stats reporting loop."""
        self._running = True
        self._client = httpx.AsyncClient(timeout=10.0)
        self._task = asyncio.create_task(self._report_loop())
        logger.info(f"Stats reporter started (interval: {settings.stats_report_interval}s)")

    async def stop(self):
        """Stop stats reporting and flush remaining."""
        self._running = False

        # Flush remaining stats
        await self._send_batch()

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
        logger.info("Stats reporter stopped")

    def record(self, stats):
        """Record a connection stats entry."""
        self._stats_queue.append({
            "service_id": stats.service_id,
            "client_ip": stats.client_ip,
            "status": stats.status,
            "duration": stats.duration,
            "bytes_sent": stats.bytes_sent,
            "bytes_received": stats.bytes_received,
            "timestamp": datetime.utcnow().isoformat()
        })

    async def _report_loop(self):
        """Main reporting loop."""
        while self._running:
            await asyncio.sleep(settings.stats_report_interval)
            await self._send_batch()

    async def _send_batch(self):
        """Send a batch of stats to controller."""
        if not self._stats_queue:
            return

        # Collect batch
        batch = []
        while self._stats_queue and len(batch) < settings.stats_batch_size:
            batch.append(self._stats_queue.popleft())

        if not batch:
            return

        url = f"{settings.controller_url}/api/v1/stats/connections"
        payload = {
            "agent_id": self.agent_id,
            "connections": batch
        }

        try:
            response = await self._client.post(url, json=payload)
            response.raise_for_status()
            logger.debug(f"Reported {len(batch)} connection stats")
        except httpx.RequestError as e:
            # Put stats back in queue
            for stat in reversed(batch):
                self._stats_queue.appendleft(stat)
            logger.warning(f"Failed to report stats (will retry): {e}")
        except Exception as e:
            logger.error(f"Error reporting stats: {e}")
