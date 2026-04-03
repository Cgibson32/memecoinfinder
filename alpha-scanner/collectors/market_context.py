"""Market context collector — BTC trend, Fear & Greed Index, meme sector momentum."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from collectors.base import BaseCollector
from models.token import MarketContext
from utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class MarketContextCollector(BaseCollector):
    """Collect macro market context for adaptive threshold adjustment."""

    name = "market_context"

    def __init__(self, session: aiohttp.ClientSession, rate_limiter: RateLimiter | None = None) -> None:
        super().__init__(session, rate_limiter)
        self.latest_context = MarketContext()

    async def collect(self) -> list[dict[str, Any]]:
        """Fetch and update market context. Returns a single-item list."""
        btc_data = await self._fetch_btc_data()
        fear_greed = await self._fetch_fear_greed()

        ctx = MarketContext()

        if btc_data:
            ctx.btc_price = btc_data.get("price", 0)
            ctx.btc_change_24h = btc_data.get("change_24h", 0)
            if ctx.btc_change_24h > 3:
                ctx.btc_trend = "bullish"
            elif ctx.btc_change_24h < -3:
                ctx.btc_trend = "bearish"
            else:
                ctx.btc_trend = "neutral"

        if fear_greed:
            ctx.fear_greed_index = fear_greed.get("value", 50)
            ctx.fear_greed_label = fear_greed.get("classification", "Neutral")

        # Meme sector trend based on BTC + sentiment combo
        if ctx.btc_trend == "bullish" and ctx.fear_greed_index > 60:
            ctx.meme_sector_trend = "bullish"
        elif ctx.btc_trend == "bearish" and ctx.fear_greed_index < 30:
            ctx.meme_sector_trend = "bearish"
        else:
            ctx.meme_sector_trend = "neutral"

        self.latest_context = ctx
        logger.info(
            "Market context: BTC %s (%.1f%%), F&G %d (%s), Meme sector: %s",
            ctx.btc_trend, ctx.btc_change_24h, ctx.fear_greed_index,
            ctx.fear_greed_label, ctx.meme_sector_trend,
        )
        return [{"type": "market_context", "context": ctx.model_dump()}]

    async def _fetch_btc_data(self) -> dict | None:
        """Fetch BTC price from CoinGecko free API."""
        data = await self.fetch_json(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": "bitcoin",
                "vs_currencies": "usd",
                "include_24hr_change": "true",
            },
        )
        if data and "bitcoin" in data:
            btc = data["bitcoin"]
            return {
                "price": btc.get("usd", 0),
                "change_24h": btc.get("usd_24h_change", 0),
            }
        return None

    async def _fetch_fear_greed(self) -> dict | None:
        """Fetch Fear & Greed Index from alternative.me."""
        data = await self.fetch_json("https://api.alternative.me/fng/?limit=1")
        if data and "data" in data and data["data"]:
            entry = data["data"][0]
            return {
                "value": int(entry.get("value", 50)),
                "classification": entry.get("value_classification", "Neutral"),
            }
        return None
