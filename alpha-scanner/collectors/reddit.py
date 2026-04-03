"""Reddit collector — scrapes subreddits for meme coin mentions via JSON API."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import aiohttp

from collectors.base import BaseCollector
from models.token import SocialSignal
from utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

SUBREDDITS = [
    "CryptoMoonShots",
    "memecoins",
    "SatoshiStreetBets",
    "solana",
    "CryptoCurrency",
]

# Regex patterns for token detection
CASHTAG_RE = re.compile(r"\$([A-Z]{2,10})\b")
EVM_ADDR_RE = re.compile(r"\b(0x[0-9a-fA-F]{40})\b")
SOL_ADDR_RE = re.compile(r"\b([1-9A-HJ-NP-Za-km-z]{32,44})\b")
BULLISH_KEYWORDS = {
    "gem", "moonshot", "100x", "early", "just launched", "hidden gem",
    "next", "moon", "pump", "bullish", "buy", "ape", "degen", "send it",
    "undervalued", "low cap", "rocket",
}

HEADERS = {"User-Agent": "AlphaScanner/1.0 (Meme Coin Research Bot)"}


class RedditCollector(BaseCollector):
    """Collect meme coin mentions from Reddit."""

    name = "reddit"

    def __init__(self, session: aiohttp.ClientSession, rate_limiter: RateLimiter | None = None) -> None:
        super().__init__(session, rate_limiter)
        self._mention_history: dict[str, list[datetime]] = {}

    async def collect(self) -> list[dict[str, Any]]:
        all_signals: list[dict[str, Any]] = []

        for sub in SUBREDDITS:
            posts = await self._fetch_subreddit(sub)
            for post in posts:
                signals = self._parse_post(post, sub)
                all_signals.extend(signals)

        logger.info("Reddit collected %d signals from %d subreddits", len(all_signals), len(SUBREDDITS))
        return all_signals

    async def _fetch_subreddit(self, subreddit: str) -> list[dict]:
        url = f"https://www.reddit.com/r/{subreddit}/new.json"
        data = await self.fetch_json(url, params={"limit": "25"}, headers=HEADERS)
        if data and "data" in data:
            return [child["data"] for child in data["data"].get("children", [])]
        return []

    def _parse_post(self, post: dict, subreddit: str) -> list[dict[str, Any]]:
        """Extract token mentions and signals from a Reddit post."""
        signals: list[dict[str, Any]] = []
        title = post.get("title", "")
        body = post.get("selftext", "")
        text = f"{title} {body}".upper()
        text_lower = f"{title} {body}".lower()

        # Find cashtags
        cashtags = CASHTAG_RE.findall(f"{title} {body}")
        # Find contract addresses
        evm_addrs = EVM_ADDR_RE.findall(f"{title} {body}")
        sol_addrs = [a for a in SOL_ADDR_RE.findall(f"{title} {body}") if len(a) >= 40]

        # Calculate sentiment from keyword presence
        sentiment = 0.0
        keyword_count = sum(1 for kw in BULLISH_KEYWORDS if kw in text_lower)
        if keyword_count > 0:
            sentiment = min(keyword_count / 5, 1.0)

        upvotes = post.get("ups", 0)
        created_utc = post.get("created_utc", 0)
        post_age_hours = max(
            (datetime.now(timezone.utc) - datetime.fromtimestamp(created_utc, tz=timezone.utc)).total_seconds() / 3600,
            0.01,
        )
        upvote_velocity = upvotes / post_age_hours

        # Create signals for each found token reference
        for symbol in set(cashtags):
            self._track_mention(symbol)
            velocity = self._get_velocity(symbol)
            signals.append({
                "type": "social",
                "source": "reddit",
                "token_symbol": symbol,
                "contract_address": "",
                "mention_count": 1,
                "sentiment_score": sentiment,
                "velocity": velocity,
                "upvote_velocity": upvote_velocity,
                "subreddit": subreddit,
                "metadata": {
                    "title": title[:200],
                    "upvotes": upvotes,
                    "subreddit": subreddit,
                    "keyword_count": keyword_count,
                },
            })

        for addr in evm_addrs + sol_addrs:
            signals.append({
                "type": "social",
                "source": "reddit",
                "token_symbol": "",
                "contract_address": addr,
                "mention_count": 1,
                "sentiment_score": sentiment,
                "velocity": 0,
                "upvote_velocity": upvote_velocity,
                "subreddit": subreddit,
                "metadata": {"title": title[:200], "upvotes": upvotes},
            })

        return signals

    def _track_mention(self, symbol: str) -> None:
        now = datetime.now(timezone.utc)
        if symbol not in self._mention_history:
            self._mention_history[symbol] = []
        self._mention_history[symbol].append(now)
        # Keep only last hour
        cutoff = now.timestamp() - 3600
        self._mention_history[symbol] = [
            t for t in self._mention_history[symbol] if t.timestamp() > cutoff
        ]

    def _get_velocity(self, symbol: str) -> float:
        """Mentions per hour for a symbol."""
        return float(len(self._mention_history.get(symbol, [])))
