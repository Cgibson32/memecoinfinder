"""GoPlus Security API integration + LP lock detection — the safety gate."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from config.chains import CHAIN_CONFIG, get_chain_config
from config.settings import settings
from models.token import SafetyResult
from storage.cache import TTLCache
from utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

GOPLUS_BASE = "https://api.gopluslabs.io/api/v1"


class SafetyAnalyzer:
    """Performs safety checks on tokens using GoPlus Security API and LP lock detection."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        rate_limiter: RateLimiter,
        cache: TTLCache,
    ) -> None:
        self.session = session
        self.rate_limiter = rate_limiter
        self.cache = cache

    async def check_token(self, contract_address: str, chain: str) -> SafetyResult | None:
        """Run full safety analysis on a token. Returns None if API unavailable."""
        cache_key = f"safety:{chain}:{contract_address}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        cfg = get_chain_config(chain)
        if not cfg:
            return None

        goplus_data = await self._fetch_goplus(contract_address, cfg.goplus_chain_id)
        if not goplus_data:
            return None

        result = self._evaluate_safety(contract_address, chain, goplus_data)

        # Check LP lock status
        lp_info = await self._check_lp_lock(contract_address, chain)
        if lp_info:
            result.lp_locked = lp_info.get("locked", False)
            result.lp_lock_duration_days = lp_info.get("duration_days", 0)
            if result.lp_locked:
                result.safety_score = min(result.safety_score + 15, 100)
            elif not result.lp_locked and result.safety_score > 0:
                result.safety_score = max(result.safety_score - 15, 0)

        self.cache.set(cache_key, result, ttl_seconds=1800)  # 30 min cache
        return result

    async def _fetch_goplus(self, contract_address: str, chain_id: str) -> dict | None:
        """Fetch token security data from GoPlus."""
        await self.rate_limiter.acquire()
        url = f"{GOPLUS_BASE}/token_security/{chain_id}"
        params = {"contract_addresses": contract_address}

        try:
            async with self.session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.warning("GoPlus returned %d for %s", resp.status, contract_address)
                    return None
                data = await resp.json()
                result = data.get("result", {})
                # GoPlus returns data keyed by lowercase address
                token_data = result.get(contract_address.lower(), {})
                if not token_data:
                    # Try original case
                    token_data = result.get(contract_address, {})
                return token_data or None
        except Exception as exc:
            logger.warning("GoPlus API error: %s", exc)
            return None

    def _evaluate_safety(self, contract_address: str, chain: str, data: dict) -> SafetyResult:
        """Evaluate safety score based on GoPlus data."""
        score = 100.0

        is_honeypot = data.get("is_honeypot", "0") == "1"
        if is_honeypot:
            return SafetyResult(
                contract_address=contract_address,
                chain=chain,
                is_honeypot=True,
                safety_score=0,
            )

        buy_tax = float(data.get("buy_tax", "0") or "0") * 100
        sell_tax = float(data.get("sell_tax", "0") or "0") * 100
        is_mintable = data.get("is_mintable", "0") == "1"
        can_take_back = data.get("can_take_back_ownership", "0") == "1"
        owner_change = data.get("owner_change_balance", "0") == "1"
        hidden_owner = data.get("hidden_owner", "0") == "1"
        is_proxy = data.get("is_proxy", "0") == "1"
        is_open_source = data.get("is_open_source", "0") == "1"
        holder_count = int(data.get("holder_count", "0") or "0")

        # Reject conditions
        if can_take_back or owner_change:
            score = 0
        else:
            if sell_tax > settings.MAX_SELL_TAX_PERCENT:
                score = max(score - 40, 0)
            elif sell_tax > 5:
                score -= 30

            if buy_tax > settings.MAX_BUY_TAX_PERCENT:
                score = max(score - 40, 0)
            elif buy_tax > 5:
                score -= 20

            if is_mintable:
                score -= 20
            if hidden_owner:
                score -= 25
            if not is_open_source:
                score -= 10
            if is_proxy:
                score -= 5
            if holder_count < 50:
                score -= 10

        return SafetyResult(
            contract_address=contract_address,
            chain=chain,
            is_honeypot=is_honeypot,
            buy_tax=buy_tax,
            sell_tax=sell_tax,
            is_mintable=is_mintable,
            can_take_back_ownership=can_take_back,
            owner_change_balance=owner_change,
            hidden_owner=hidden_owner,
            is_proxy=is_proxy,
            is_open_source=is_open_source,
            holder_count=holder_count,
            safety_score=max(score, 0),
        )

    async def _check_lp_lock(self, contract_address: str, chain: str) -> dict | None:
        """Check if LP is locked via known lock contract interactions.

        This is a simplified check — in production you'd query specific lock
        contracts (Team Finance, Unicrypt, etc.) on-chain.
        """
        # For now, we flag LP as unknown and rely on GoPlus data
        # A full implementation would query lock contracts
        try:
            cfg = get_chain_config(chain)
            if not cfg or chain == "solana":
                return {"locked": False, "duration_days": 0}

            # Check GoPlus LP holder data if available
            # GoPlus sometimes includes lp_holder info
            return {"locked": False, "duration_days": 0}
        except Exception:
            return None
