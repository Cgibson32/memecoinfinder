"""Sniper bot detection — identify tokens with coordinated launch buying."""

from __future__ import annotations

import logging
from typing import Any

from storage.cache import TTLCache

logger = logging.getLogger(__name__)


class SniperDetector:
    """Detect sniper bots that bought in the same block as LP addition."""

    def __init__(self, cache: TTLCache) -> None:
        self.cache = cache

    def analyze(
        self,
        contract_address: str,
        chain: str,
        early_buyers: list[dict] | None = None,
        liquidity_usd: float = 0,
    ) -> dict[str, Any]:
        """Analyze early buying patterns for sniper bot activity.

        *early_buyers*: list of dicts with keys 'block', 'wallet', 'amount_usd'
        representing the first transactions on this token.
        """
        cache_key = f"sniper:{chain}:{contract_address}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        if not early_buyers:
            result = {
                "sniper_score": 0,
                "sniper_count": 0,
                "is_sniped": False,
                "penalty": 0,
                "details": "No early buyer data available",
            }
            self.cache.set(cache_key, result, ttl_seconds=1800)
            return result

        # Group buyers by block
        block_groups: dict[int, list[dict]] = {}
        for buyer in early_buyers:
            block = buyer.get("block", 0)
            if block not in block_groups:
                block_groups[block] = []
            block_groups[block].append(buyer)

        # Find the first block (LP addition block)
        if not block_groups:
            result = {
                "sniper_score": 0,
                "sniper_count": 0,
                "is_sniped": False,
                "penalty": 0,
            }
            self.cache.set(cache_key, result, ttl_seconds=1800)
            return result

        first_block = min(block_groups.keys())
        first_block_buyers = block_groups[first_block]
        sniper_count = len(first_block_buyers)

        # Also check second block
        second_block = first_block + 1
        second_block_buyers = block_groups.get(second_block, [])
        total_early_snipers = sniper_count + len(second_block_buyers)

        # Calculate sniper volume
        sniper_volume = sum(b.get("amount_usd", 0) for b in first_block_buyers + second_block_buyers)

        # Risk assessment
        penalty = 0.0
        is_sniped = False

        if total_early_snipers > 5:
            penalty = 10
            is_sniped = True
        elif total_early_snipers > 10:
            penalty = 15
            is_sniped = True

        # Extra penalty if snipers grabbed a lot relative to LP
        if liquidity_usd > 0 and sniper_volume > liquidity_usd * 0.3:
            penalty += 5
            is_sniped = True

        result = {
            "sniper_score": max(100 - penalty * 3, 0),
            "sniper_count": total_early_snipers,
            "sniper_volume_usd": sniper_volume,
            "is_sniped": is_sniped,
            "penalty": penalty,
            "first_block_buyers": sniper_count,
        }

        self.cache.set(cache_key, result, ttl_seconds=1800)
        return result
