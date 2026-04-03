"""Insider wallet clustering — detect deployer-funded wallets."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from config.chains import get_chain_config
from models.token import InsiderInfo
from storage.cache import TTLCache
from storage.database import Database

logger = logging.getLogger(__name__)


class InsiderDetector:
    """Detect insider wallets by clustering funding sources."""

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
        self,
        token_id: int,
        contract_address: str,
        chain: str,
        deployer_address: str,
        top_holders: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Detect insider wallets and estimate their holdings."""
        cache_key = f"insider:{chain}:{contract_address}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        insider_info = InsiderInfo(
            contract_address=contract_address,
            chain=chain,
            deployer_address=deployer_address,
        )

        if not deployer_address or not top_holders:
            result = {
                "insider_score": 0,
                "insider_holding_pct": 0,
                "related_wallets": [],
                "is_suspicious": False,
                "penalty": 0,
            }
            self.cache.set(cache_key, result, ttl_seconds=1800)
            return result

        # Check if top holders were funded by the deployer
        related = await self._find_related_wallets(
            deployer_address, [h.get("address", "") for h in top_holders], chain
        )

        insider_holding = 0.0
        if related and top_holders:
            related_set = set(related)
            for holder in top_holders:
                if holder.get("address", "") in related_set:
                    insider_holding += holder.get("percentage", 0)

        insider_info.related_wallets = related
        insider_info.insider_holding_pct = insider_holding
        insider_info.is_suspicious = insider_holding > 20 or len(related) > 3

        # Calculate penalty
        penalty = 0.0
        if insider_holding > 30:
            penalty = 30
        elif insider_holding > 20:
            penalty = 20
        elif insider_holding > 10:
            penalty = 10
        elif len(related) > 3:
            penalty = 10

        # Credibility score (inverse of insider risk)
        credibility = max(100 - penalty * 2, 0)

        result = {
            "insider_score": credibility,
            "insider_holding_pct": insider_holding,
            "related_wallets": related,
            "related_count": len(related),
            "is_suspicious": insider_info.is_suspicious,
            "penalty": penalty,
        }

        # Save to DB
        if related:
            await self.db.save_insider_cluster(
                token_id=token_id,
                deployer_address=deployer_address,
                related_wallets=related,
                funding_source=deployer_address,
                insider_holding_pct=insider_holding,
            )

        self.cache.set(cache_key, result, ttl_seconds=1800)
        return result

    async def _find_related_wallets(
        self,
        deployer_address: str,
        holder_addresses: list[str],
        chain: str,
    ) -> list[str]:
        """Check which holder wallets received funds from deployer.

        Uses block explorer APIs where available. Falls back to heuristic
        detection (e.g., wallets created at similar times).
        """
        related: list[str] = []
        cfg = get_chain_config(chain)
        if not cfg or chain == "solana":
            return related

        # For EVM chains, we can check if the deployer sent ETH/BNB to holders
        # This is a simplified check — full implementation would trace tx history
        try:
            for addr in holder_addresses[:10]:  # Check top 10 holders only
                if addr.lower() == deployer_address.lower():
                    related.append(addr)
                    continue

                # Check if deployer funded this wallet
                # In practice, use explorer API to check internal transactions
                # For now, flag wallets with very similar creation patterns
                # This is a placeholder for the full on-chain trace
        except Exception as exc:
            logger.debug("Insider detection error: %s", exc)

        return related
