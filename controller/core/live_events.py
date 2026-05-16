"""In-memory live event bus for SSE streaming to browser.

Holds a small history ring buffer per channel and notifies all SSE subscribers
when new events arrive. Thread-safe: sync endpoints call push() from the uvicorn
thread pool; SSE generator runs on the event loop. The stored loop reference lets
call_soon_threadsafe bridge the two.
"""

import asyncio
from collections import deque
from typing import Dict, List, Optional


class LiveEventBus:
    HISTORY = 100

    def __init__(self):
        self._history: Dict[str, deque] = {
            "incoming": deque(maxlen=self.HISTORY),
            "dns":      deque(maxlen=self.HISTORY),
            "forward":  deque(maxlen=self.HISTORY),
            "email":    deque(maxlen=self.HISTORY),
        }
        self._subscribers: List[asyncio.Queue] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def push(self, channel: str, event: dict) -> None:
        evt = {**event, "channel": channel}
        if channel in self._history:
            self._history[channel].appendleft(evt)
        if not self._subscribers:
            return
        if self._loop and self._loop.is_running():
            for q in list(self._subscribers):
                self._loop.call_soon_threadsafe(self._try_put, q, evt)
        else:
            for q in list(self._subscribers):
                self._try_put(q, evt)

    def _try_put(self, q: asyncio.Queue, evt: dict) -> None:
        try:
            q.put_nowait(evt)
        except asyncio.QueueFull:
            pass

    def get_history(self, channel: Optional[str] = None) -> list:
        if channel:
            return list(self._history.get(channel, []))
        result = []
        for ch_events in self._history.values():
            result.extend(ch_events)
        return result

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass


live_events = LiveEventBus()
