"""Per-IP connection rate limiting to prevent abuse and exhaustion."""

import asyncio
import logging
import time
from collections import defaultdict
from typing import Callable, Awaitable, Optional

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Limit new connections per client IP per time window.
    Over-limit IPs can be dropped and optionally auto-blocked (reported to controller).
    """

    def __init__(
        self,
        max_connections_per_minute: int = 60,
        window_seconds: int = 60,
        on_rate_limit: Optional[Callable[[str, int], Awaitable[None]]] = None,
    ):
        self.max_per_window = max_connections_per_minute
        self.window_seconds = window_seconds
        self.on_rate_limit = on_rate_limit  # async (client_ip, count) -> None
        # ip -> list of timestamps of recent connection attempts
        self._counts: defaultdict[str, list] = defaultdict(list)
        self._lock = asyncio.Lock()

    def _prune(self, ip: str, now: float) -> None:
        cutoff = now - self.window_seconds
        self._counts[ip] = [t for t in self._counts[ip] if t > cutoff]

    async def allow(self, client_ip: str, _service_id: Optional[int] = None) -> bool:
        """
        Returns True if the connection is allowed, False if over limit.
        If over limit and on_rate_limit is set, calls it (e.g. to auto-block the IP).
        """
        now = time.monotonic()
        async with self._lock:
            self._prune(client_ip, now)
            self._counts[client_ip].append(now)
            count = len(self._counts[client_ip])

        if count <= self.max_per_window:
            return True

        # Over limit - remove the one we just added so we don't double-count
        async with self._lock:
            if self._counts[client_ip]:
                self._counts[client_ip].pop()
        logger.warning(f"Rate limit exceeded for {client_ip} ({count} connections in {self.window_seconds}s)")
        if self.on_rate_limit:
            try:
                await self.on_rate_limit(client_ip, count)
            except Exception as e:
                logger.warning(f"Rate limit callback error: {e}")
        return False
