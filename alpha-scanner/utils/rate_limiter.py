"""Async token-bucket rate limiter using a sliding window."""

from __future__ import annotations

import asyncio
import time
from collections import deque


class RateLimiter:
    """Limits requests to *max_per_minute* within a rolling 60-second window."""

    def __init__(self, max_per_minute: int) -> None:
        self._max = max_per_minute
        self._window: deque[float] = deque()
        self._lock = asyncio.Lock()

    def _prune(self) -> None:
        cutoff = time.monotonic() - 60.0
        while self._window and self._window[0] < cutoff:
            self._window.popleft()

    async def acquire(self) -> None:
        """Block until a request slot is available."""
        while True:
            async with self._lock:
                self._prune()
                if len(self._window) < self._max:
                    self._window.append(time.monotonic())
                    return
                wait = 60.0 - (time.monotonic() - self._window[0])
            await asyncio.sleep(max(wait, 0.1))

    async def wait_if_needed(self) -> bool:
        """Non-blocking check. Returns True if a slot was acquired, False otherwise."""
        async with self._lock:
            self._prune()
            if len(self._window) < self._max:
                self._window.append(time.monotonic())
                return True
            return False

    @property
    def current_usage(self) -> int:
        self._prune()
        return len(self._window)
