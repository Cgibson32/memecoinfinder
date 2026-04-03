"""Abstract base collector with retry logic, rate limiting, and health tracking."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

import aiohttp

from utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class BaseCollector(ABC):
    """Abstract base class that all collectors must inherit from."""

    name: str = "base"
    max_retries: int = 3

    def __init__(
        self,
        session: aiohttp.ClientSession,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.session = session
        self.rate_limiter = rate_limiter
        self.last_successful_run: float = 0.0
        self.consecutive_failures: int = 0
        self._running = True

    @abstractmethod
    async def collect(self) -> list[Any]:
        """Perform one collection cycle. Must be implemented by subclasses."""
        ...

    async def run_once(self) -> list[Any]:
        """Execute a single collection cycle with error handling."""
        try:
            results = await self.collect()
            self.last_successful_run = time.monotonic()
            self.consecutive_failures = 0
            return results
        except Exception as exc:
            self.consecutive_failures += 1
            logger.error(
                "%s collector error (failure #%d): %s",
                self.name, self.consecutive_failures, exc,
            )
            if self.consecutive_failures >= 5:
                logger.warning(
                    "%s collector has %d consecutive failures!",
                    self.name, self.consecutive_failures,
                )
            return []

    async def fetch_json(
        self, url: str, params: dict | None = None, headers: dict | None = None,
    ) -> Any:
        """Fetch JSON from *url* with retry logic and rate limiting."""
        for attempt in range(1, self.max_retries + 1):
            if self.rate_limiter:
                await self.rate_limiter.acquire()
            try:
                async with self.session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 429:
                        retry_after = int(resp.headers.get("Retry-After", "60"))
                        logger.warning("%s rate limited, waiting %ds", self.name, retry_after)
                        await asyncio.sleep(retry_after)
                        continue
                    resp.raise_for_status()
                    return await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                wait = 2 ** attempt
                logger.warning(
                    "%s fetch %s attempt %d/%d failed: %s — retrying in %ds",
                    self.name, url, attempt, self.max_retries, exc, wait,
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(wait)
        return None

    def stop(self) -> None:
        self._running = False

    @property
    def is_healthy(self) -> bool:
        return self.consecutive_failures < 5

    @property
    def status_summary(self) -> dict:
        return {
            "name": self.name,
            "healthy": self.is_healthy,
            "consecutive_failures": self.consecutive_failures,
            "last_success_ago": (
                f"{time.monotonic() - self.last_successful_run:.0f}s"
                if self.last_successful_run > 0 else "never"
            ),
        }
