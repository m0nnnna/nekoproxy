import asyncio
import logging
from datetime import datetime, timedelta

from controller.config import settings
from controller.database.database import SessionLocal
from controller.database.repositories import AgentRepository, ConnectionStatRepository
from shared.models.common import HealthStatus

logger = logging.getLogger(__name__)


class HealthMonitor:
    """Background task that monitors agent health and cleans up old stats."""

    def __init__(self):
        self._running = False
        self._task: asyncio.Task = None

    async def start(self):
        """Start the health monitor background task."""
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Health monitor started")

    async def stop(self):
        """Stop the health monitor."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Health monitor stopped")

    async def _monitor_loop(self):
        """Main monitoring loop."""
        while self._running:
            try:
                await self._check_agents()
                await self._cleanup_stats()
            except Exception as e:
                logger.error(f"Error in health monitor: {e}")

            # Run every 30 seconds
            await asyncio.sleep(30)

    async def _check_agents(self):
        """Check agent health based on heartbeat timeout."""
        db = SessionLocal()
        try:
            agent_repo = AgentRepository(db)
            agents = agent_repo.get_all()

            timeout = timedelta(seconds=settings.heartbeat_timeout)
            now = datetime.utcnow()

            for agent in agents:
                if agent.status == HealthStatus.HEALTHY:
                    if agent.last_heartbeat:
                        time_since = now - agent.last_heartbeat
                        if time_since > timeout:
                            agent_repo.mark_unhealthy(agent.id)
                            logger.warning(
                                f"Agent {agent.hostname} marked unhealthy "
                                f"(no heartbeat for {time_since.seconds}s)"
                            )
                    else:
                        # No heartbeat ever received
                        agent_repo.mark_unhealthy(agent.id)
        finally:
            db.close()

    async def _cleanup_stats(self):
        """Clean up old connection statistics."""
        # Only run cleanup once per hour
        if not hasattr(self, '_last_cleanup'):
            self._last_cleanup = datetime.utcnow()

        if datetime.utcnow() - self._last_cleanup < timedelta(hours=1):
            return

        db = SessionLocal()
        try:
            stat_repo = ConnectionStatRepository(db)
            deleted = stat_repo.cleanup_old(days=settings.stats_retention_days)
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old connection stats")
            self._last_cleanup = datetime.utcnow()
        finally:
            db.close()
