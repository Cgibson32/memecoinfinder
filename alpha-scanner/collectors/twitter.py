"""Twitter/Crypto Twitter collector via Nitter RSS feeds."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import aiohttp

from collectors.base import BaseCollector
from config.settings import settings
from utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Popular CT accounts to monitor (configurable)
CT_ACCOUNTS = [
    "lookonchain",
    "whale_alert",
    "DefiIgnas",
    "CryptoGodJohn",
    "blaboratory",
]

CASHTAG_RE = re.compile(r"\$([A-Z]{2,10})\b")
EVM_ADDR_RE = re.compile(r"\b(0x[0-9a-fA-F]{40})\b")


class TwitterCollector(BaseCollector):
    """Collect crypto token mentions from Nitter RSS feeds."""

    name = "twitter"

    def __init__(self, session: aiohttp.ClientSession, rate_limiter: RateLimiter | None = None) -> None:
        super().__init__(session, rate_limiter)
        self._nitter_base = settings.NITTER_INSTANCE.rstrip("/")
        self._mention_history: dict[str, list[datetime]] = {}

    async def collect(self) -> list[dict[str, Any]]:
        signals: list[dict[str, Any]] = []

        for account in CT_ACCOUNTS:
            items = await self._fetch_feed(account)
            for item in items:
                parsed = self._parse_item(item, account)
                signals.extend(parsed)

        logger.info("Twitter collected %d signals from %d accounts", len(signals), len(CT_ACCOUNTS))
        return signals

    async def _fetch_feed(self, account: str) -> list[dict]:
        """Fetch RSS feed for a Nitter account."""
        url = f"{self._nitter_base}/{account}/rss"
        try:
            import feedparser  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("feedparser not installed, Twitter collector disabled")
            return []

        try:
            if self.rate_limiter:
                await self.rate_limiter.acquire()
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.debug("Nitter feed %s returned %d", account, resp.status)
                    return []
                text = await resp.text()
                feed = feedparser.parse(text)
                return feed.entries[:20]
        except Exception as exc:
            logger.debug("Failed to fetch Nitter feed for %s: %s", account, exc)
            return []

    def _parse_item(self, item: dict, account: str) -> list[dict[str, Any]]:
        """Extract token mentions from an RSS item."""
        signals: list[dict[str, Any]] = []
        title = item.get("title", "")
        summary = item.get("summary", "")
        text = f"{title} {summary}"

        cashtags = CASHTAG_RE.findall(text)
        evm_addrs = EVM_ADDR_RE.findall(text)

        published = item.get("published_parsed")
        if published:
            try:
                pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
            except Exception:
                pub_dt = datetime.now(timezone.utc)
        else:
            pub_dt = datetime.now(timezone.utc)

        for symbol in set(cashtags):
            self._track_mention(symbol)
            signals.append({
                "type": "social",
                "source": "twitter",
                "token_symbol": symbol,
                "contract_address": "",
                "mention_count": 1,
                "sentiment_score": 0.5,
                "velocity": self._get_velocity(symbol),
                "metadata": {
                    "account": account,
                    "text": text[:300],
                    "published": pub_dt.isoformat(),
                },
            })

        for addr in evm_addrs:
            signals.append({
                "type": "social",
                "source": "twitter",
                "token_symbol": "",
                "contract_address": addr,
                "mention_count": 1,
                "sentiment_score": 0.5,
                "velocity": 0,
                "metadata": {"account": account, "text": text[:300]},
            })

        return signals

    def _track_mention(self, symbol: str) -> None:
        now = datetime.now(timezone.utc)
        if symbol not in self._mention_history:
            self._mention_history[symbol] = []
        self._mention_history[symbol].append(now)
        cutoff = now.timestamp() - 3600
        self._mention_history[symbol] = [
            t for t in self._mention_history[symbol] if t.timestamp() > cutoff
        ]

    def _get_velocity(self, symbol: str) -> float:
        return float(len(self._mention_history.get(symbol, [])))
