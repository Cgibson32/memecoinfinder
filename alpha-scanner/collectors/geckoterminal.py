"""GeckoTerminal API collector — secondary data source for cross-referencing."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from collectors.base import BaseCollector
from config.chains import CHAIN_CONFIG
from config.settings import settings
from utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

BASE_URL = "https://api.geckoterminal.com/api/v2"


class GeckoTerminalCollector(BaseCollector):
    """Collect trending and new pools from GeckoTerminal."""

    name = "geckoterminal"

    def __init__(self, session: aiohttp.ClientSession, rate_limiter: RateLimiter) -> None:
        super().__init__(session, rate_limiter)

    async def collect(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

        # Fetch trending pools across all networks
        trending = await self._fetch_trending_pools()
        for pool in trending:
            parsed = self._parse_pool(pool)
            if parsed:
                results.append(parsed)

        # Fetch new pools per active chain
        for chain in settings.ACTIVE_CHAINS:
            cfg = CHAIN_CONFIG.get(chain)
            if not cfg:
                continue
            new_pools = await self._fetch_new_pools(cfg.geckoterminal_id)
            for pool in new_pools:
                parsed = self._parse_pool(pool, chain_override=chain)
                if parsed:
                    results.append(parsed)

        logger.info("GeckoTerminal collected %d pools", len(results))
        return results

    async def _fetch_trending_pools(self) -> list[dict]:
        data = await self.fetch_json(f"{BASE_URL}/networks/trending_pools")
        if data and "data" in data:
            return data["data"]
        return []

    async def _fetch_new_pools(self, network: str) -> list[dict]:
        data = await self.fetch_json(f"{BASE_URL}/networks/{network}/new_pools")
        if data and "data" in data:
            return data["data"]
        return []

    async def fetch_token_info(self, network: str, token_address: str) -> dict | None:
        """Fetch detailed token info (used by other modules)."""
        data = await self.fetch_json(
            f"{BASE_URL}/networks/{network}/tokens/{token_address}"
        )
        if data and "data" in data:
            return data["data"]
        return None

    def _parse_pool(self, pool: dict, chain_override: str = "") -> dict[str, Any] | None:
        try:
            attrs = pool.get("attributes", {})
            relationships = pool.get("relationships", {})

            # Determine chain from the pool ID (format: network_address)
            pool_id = pool.get("id", "")
            network = pool_id.split("_")[0] if "_" in pool_id else ""

            # Map GeckoTerminal network name back to our chain name
            chain = chain_override
            if not chain:
                for c, cfg in CHAIN_CONFIG.items():
                    if cfg.geckoterminal_id == network:
                        chain = c
                        break

            if chain not in settings.ACTIVE_CHAINS:
                return None

            pool_address = attrs.get("address", "")
            token_name = attrs.get("name", "")
            base_token_price = float(attrs.get("base_token_price_usd", 0) or 0)

            volume_h24 = float(attrs.get("volume_usd", {}).get("h24", 0) or 0)
            reserve = float(attrs.get("reserve_in_usd", 0) or 0)

            # Extract token address from relationships if available
            base_token = relationships.get("base_token", {}).get("data", {})
            token_id = base_token.get("id", "")
            token_address = token_id.split("_")[-1] if "_" in token_id else ""

            return {
                "token": {
                    "contract_address": token_address,
                    "chain": chain,
                    "token_name": token_name,
                    "token_symbol": attrs.get("name", "").split("/")[0].strip() if "/" in attrs.get("name", "") else "",
                    "pair_address": pool_address,
                    "dex": attrs.get("dex_id", ""),
                },
                "metrics": {
                    "contract_address": token_address,
                    "chain": chain,
                    "price_usd": base_token_price,
                    "volume_h24": volume_h24,
                    "liquidity_usd": reserve,
                    "price_change_h24": float(attrs.get("price_change_percentage", {}).get("h24", 0) or 0),
                },
                "source": "geckoterminal",
            }
        except Exception as exc:
            logger.debug("Failed to parse GeckoTerminal pool: %s", exc)
            return None
