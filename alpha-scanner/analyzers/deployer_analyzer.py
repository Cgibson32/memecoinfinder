"""Deployer wallet history analysis — detect serial ruggers."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from config.chains import get_chain_config
from storage.cache import TTLCache
from storage.database import Database

logger = logging.getLogger(__name__)


class DeployerAnalyzer:
    """Analyze deployer wallet history to detect serial rug-pullers."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        db: Database,
        cache: TTLCache,
    ) -> None:
        self.session = session
        self.db = db
        self.cache = cache

    async def analyze(
        self, deployer_address: str, token_id: int, chain: str
    ) -> dict[str, Any]:
        """Check deployer history. Returns analysis with risk assessment."""
        if not deployer_address:
            return {
                "deployer_risk": "unknown",
                "deployer_score": 50,
                "previous_tokens": 0,
                "rugged_tokens": 0,
                "successful_tokens": 0,
            }

        cache_key = f"deployer:{chain}:{deployer_address}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        # Check our database first
        history = await self.db.get_deployer_history(deployer_address)

        total = len(history)
        rugged = sum(1 for h in history if h["outcome"] == "rugged")
        dead = sum(1 for h in history if h["outcome"] == "dead")
        successful = sum(1 for h in history if h["outcome"] == "success")
        lp_removed = sum(1 for h in history if h.get("lp_removed_within_24h"))

        # Calculate risk
        if total == 0:
            risk = "unknown"
            score = 50
        elif rugged >= 2 or lp_removed >= 2:
            risk = "serial_rugger"
            score = 0
        elif rugged >= 1:
            risk = "high"
            score = 20
        elif dead >= 3 and successful == 0:
            risk = "high"
            score = 25
        elif successful > 0 and rugged == 0:
            risk = "clean"
            score = 85
        else:
            risk = "moderate"
            score = 50

        # Record this token for the deployer
        await self.db.save_deployer_history(
            deployer_address=deployer_address,
            token_id=token_id,
            chain=chain,
            outcome="active",
        )

        result = {
            "deployer_risk": risk,
            "deployer_score": score,
            "deployer_address": deployer_address,
            "previous_tokens": total,
            "rugged_tokens": rugged,
            "dead_tokens": dead,
            "successful_tokens": successful,
            "lp_removed_count": lp_removed,
            "is_rejected": risk == "serial_rugger",
        }

        self.cache.set(cache_key, result, ttl_seconds=3600)
        return result

    async def update_outcome(
        self, deployer_address: str, token_id: int, chain: str, outcome: str
    ) -> None:
        """Update the outcome for a deployer's token."""
        await self.db.save_deployer_history(
            deployer_address=deployer_address,
            token_id=token_id,
            chain=chain,
            outcome=outcome,
        )
        # Invalidate cache
        self.cache.delete(f"deployer:{chain}:{deployer_address}")
