"""Google Trends spike detector using pytrends."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from collectors.base import BaseCollector

logger = logging.getLogger(__name__)


class GoogleTrendsCollector(BaseCollector):
    """Detect Google Trends spikes for tokens on the watchlist."""

    name = "google_trends"
    _MAX_QUERIES_PER_CYCLE = 5

    def __init__(self, session: Any = None, rate_limiter: Any = None) -> None:
        super().__init__(session, rate_limiter)  # type: ignore[arg-type]
        self._watchlist: list[dict[str, str]] = []

    def set_watchlist(self, tokens: list[dict[str, str]]) -> None:
        """Update the list of tokens to check. Each dict has 'symbol' and 'name'."""
        self._watchlist = tokens[: self._MAX_QUERIES_PER_CYCLE]

    async def collect(self) -> list[dict[str, Any]]:
        if not self._watchlist:
            return []

        signals: list[dict[str, Any]] = []

        for token_info in self._watchlist[: self._MAX_QUERIES_PER_CYCLE]:
            symbol = token_info.get("symbol", "")
            name = token_info.get("name", symbol)
            query = f"{name} crypto"

            spike = await self._check_trend(query)
            if spike and spike.get("spike_detected"):
                signals.append({
                    "type": "social",
                    "source": "google_trends",
                    "token_symbol": symbol,
                    "contract_address": token_info.get("contract_address", ""),
                    "mention_count": 1,
                    "sentiment_score": 0.8,
                    "velocity": spike.get("spike_ratio", 0),
                    "metadata": {
                        "query": query,
                        "spike_ratio": spike.get("spike_ratio", 0),
                        "current_interest": spike.get("current", 0),
                        "avg_interest": spike.get("average", 0),
                    },
                })

        if signals:
            logger.info("Google Trends detected %d spikes", len(signals))
        return signals

    async def _check_trend(self, query: str) -> dict[str, Any] | None:
        """Check Google Trends for a query. Returns spike info or None."""
        try:
            from pytrends.request import TrendReq  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("pytrends not installed")
            return None

        try:
            # Run in executor to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._sync_check_trend, query)
            return result
        except Exception as exc:
            logger.debug("Google Trends check failed for '%s': %s", query, exc)
            return None

    @staticmethod
    def _sync_check_trend(query: str) -> dict[str, Any] | None:
        """Synchronous Google Trends check (run in executor)."""
        from pytrends.request import TrendReq  # type: ignore[import-untyped]

        pytrends = TrendReq(hl="en-US", tz=0, timeout=(5, 10))
        pytrends.build_payload([query], cat=0, timeframe="now 7-d")
        df = pytrends.interest_over_time()

        if df.empty or query not in df.columns:
            return None

        values = df[query].tolist()
        if len(values) < 10:
            return None

        # Compare last 4 hours (recent points) vs previous average
        recent = values[-4:]
        previous = values[:-4]
        avg_previous = sum(previous) / len(previous) if previous else 1
        avg_recent = sum(recent) / len(recent)

        if avg_previous <= 0:
            avg_previous = 1

        spike_ratio = avg_recent / avg_previous

        return {
            "spike_detected": spike_ratio >= 2.0,  # >200% increase
            "spike_ratio": spike_ratio,
            "current": avg_recent,
            "average": avg_previous,
        }
