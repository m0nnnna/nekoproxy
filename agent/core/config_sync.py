"""Configuration synchronization with controller."""

import asyncio
import logging
from typing import Optional, Callable

import httpx

from agent.config import settings
from shared.models import AgentConfig

logger = logging.getLogger(__name__)


class ConfigSync:
    """Synchronizes configuration from controller."""

    def __init__(
        self,
        agent_id: int,
        on_config_update: Callable[[AgentConfig], None]
    ):
        self.agent_id = agent_id
        self.on_config_update = on_config_update
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None
        self._current_version: int = 0

    async def start(self):
        """Start configuration sync loop."""
        self._running = True
        self._client = httpx.AsyncClient(timeout=10.0)
        self._task = asyncio.create_task(self._sync_loop())
        logger.info("Config sync started")

    async def stop(self):
        """Stop configuration sync."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
        logger.info("Config sync stopped")

    async def fetch_config(self) -> Optional[AgentConfig]:
        """Fetch current configuration from controller."""
        url = f"{settings.controller_url}/api/v1/agents/{self.agent_id}/config"

        try:
            response = await self._client.get(url)
            response.raise_for_status()
            data = response.json()
            return AgentConfig(**data)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.error("Agent not found on controller")
            else:
                logger.error(f"Failed to fetch config: {e}")
            return None
        except httpx.RequestError as e:
            logger.warning(f"Controller unreachable: {e}")
            return None
        except Exception as e:
            logger.error(f"Error parsing config: {e}")
            return None

    async def force_sync(self):
        """Force an immediate config sync, ignoring version check."""
        logger.info("Forcing immediate config sync")
        try:
            config = await self.fetch_config()
            if config:
                logger.info(f"Force sync: applying config version {config.config_version}")
                self._current_version = config.config_version
                self.on_config_update(config)
                return True
            else:
                logger.error("Force sync: failed to fetch config")
                return False
        except Exception as e:
            logger.error(f"Force sync error: {e}")
            return False

    async def _sync_loop(self):
        """Main sync loop - poll for config changes."""
        # Fetch initial config
        initial_config = await self.fetch_config()
        if initial_config:
            self._current_version = initial_config.config_version
            self.on_config_update(initial_config)

        while self._running:
            # Wait before next poll
            await asyncio.sleep(30)

            try:
                config = await self.fetch_config()
                if config and config.config_version != self._current_version:
                    logger.info(
                        f"Config updated: version {self._current_version} -> {config.config_version}"
                    )
                    self._current_version = config.config_version
                    self.on_config_update(config)
            except Exception as e:
                logger.error(f"Config sync error: {e}")
