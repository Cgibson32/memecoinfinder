"""DEXScreener API collector — PRIMARY data source for token discovery."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import aiohttp

from collectors.base import BaseCollector
from config.settings import settings
from models.token import MetricsSnapshot, Token
from utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

BASE_URL = "https://api.dexscreener.com"


class DexScreenerCollector(BaseCollector):
    """Collect new and trending tokens from DEXScreener."""

    name = "dexscreener"

    def __init__(self, session: aiohttp.ClientSession, rate_limiter: RateLimiter) -> None:
        super().__init__(session, rate_limiter)
        self._seen_addresses: set[str] = set()

    async def collect(self) -> list[dict[str, Any]]:
        """Fetch latest boosted tokens, profiles, and enrich with pair data."""
        results: list[dict[str, Any]] = []

        boosts = await self._fetch_boosted_tokens()
        profiles = await self._fetch_token_profiles()

        # Merge unique tokens from both sources
        token_map: dict[str, dict] = {}
        for item in boosts + profiles:
            addr = item.get("tokenAddress", "")
            chain = item.get("chainId", "")
            if not addr or not chain:
                continue
            if chain.lower() not in settings.ACTIVE_CHAINS:
                continue
            key = f"{chain}:{addr}"
            if key not in token_map:
                token_map[key] = {
                    "contract_address": addr,
                    "chain": chain.lower(),
                    "token_name": item.get("description", ""),
                    "token_symbol": item.get("symbol", ""),
                    "icon": item.get("icon", ""),
                    "from_boost": item in boosts,
                    "from_profile": item in profiles,
                }

        # Batch fetch pair data for new tokens
        addresses_to_fetch: list[str] = []
        chain_for_addr: dict[str, str] = {}
        for key, info in token_map.items():
            if key not in self._seen_addresses:
                addresses_to_fetch.append(info["contract_address"])
                chain_for_addr[info["contract_address"]] = info["chain"]
                self._seen_addresses.add(key)

        # DEXScreener /latest/dex/tokens/ supports up to 30 comma-separated addresses
        for i in range(0, len(addresses_to_fetch), 30):
            batch = addresses_to_fetch[i : i + 30]
            pair_data = await self._fetch_token_pairs(batch)
            if not pair_data:
                continue
            for pair in pair_data:
                enriched = self._parse_pair(pair)
                if enriched and self._passes_filters(enriched):
                    results.append(enriched)

        logger.info("DEXScreener collected %d tokens (%d new addresses)", len(results), len(addresses_to_fetch))
        return results

    async def _fetch_boosted_tokens(self) -> list[dict]:
        data = await self.fetch_json(f"{BASE_URL}/token-boosts/latest/v1")
        if isinstance(data, list):
            return data
        return []

    async def _fetch_token_profiles(self) -> list[dict]:
        data = await self.fetch_json(f"{BASE_URL}/token-profiles/latest/v1")
        if isinstance(data, list):
            return data
        return []

    async def _fetch_token_pairs(self, addresses: list[str]) -> list[dict]:
        joined = ",".join(addresses)
        # Correct endpoint: /latest/dex/tokens/ (supports up to 30 addresses)
        data = await self.fetch_json(f"{BASE_URL}/latest/dex/tokens/{joined}")
        if data and "pairs" in data and isinstance(data["pairs"], list):
            return data["pairs"]
        if isinstance(data, list):
            return data
        return []

    async def fetch_pair(self, chain_id: str, pair_address: str) -> dict | None:
        """Fetch data for a specific pair (used by other modules)."""
        data = await self.fetch_json(f"{BASE_URL}/latest/dex/pairs/{chain_id}/{pair_address}")
        if data and "pairs" in data and data["pairs"]:
            return self._parse_pair(data["pairs"][0])
        return None

    def _parse_pair(self, pair: dict) -> dict[str, Any] | None:
        """Extract relevant fields from a DEXScreener pair object."""
        try:
            base_token = pair.get("baseToken", {})
            chain = pair.get("chainId", "").lower()
            if chain not in settings.ACTIVE_CHAINS:
                return None

            txns = pair.get("txns", {})
            volume = pair.get("volume", {})
            price_change = pair.get("priceChange", {})
            liquidity = pair.get("liquidity", {})

            created_at = pair.get("pairCreatedAt")
            if created_at:
                created_at = datetime.fromtimestamp(created_at / 1000, tz=timezone.utc)

            token = Token(
                contract_address=base_token.get("address", ""),
                chain=chain,
                token_name=base_token.get("name", ""),
                token_symbol=base_token.get("symbol", ""),
                pair_address=pair.get("pairAddress", ""),
                dex=pair.get("dexId", ""),
            )

            metrics = MetricsSnapshot(
                contract_address=token.contract_address,
                chain=chain,
                price_usd=float(pair.get("priceUsd", 0) or 0),
                volume_m5=float(volume.get("m5", 0) or 0),
                volume_h1=float(volume.get("h1", 0) or 0),
                volume_h6=float(volume.get("h6", 0) or 0),
                volume_h24=float(volume.get("h24", 0) or 0),
                liquidity_usd=float(liquidity.get("usd", 0) or 0),
                market_cap=float(pair.get("marketCap", 0) or 0),
                fdv=float(pair.get("fdv", 0) or 0),
                buys_m5=int(txns.get("m5", {}).get("buys", 0) or 0),
                sells_m5=int(txns.get("m5", {}).get("sells", 0) or 0),
                buys_h1=int(txns.get("h1", {}).get("buys", 0) or 0),
                sells_h1=int(txns.get("h1", {}).get("sells", 0) or 0),
                buys_h6=int(txns.get("h6", {}).get("buys", 0) or 0),
                sells_h6=int(txns.get("h6", {}).get("sells", 0) or 0),
                buys_h24=int(txns.get("h24", {}).get("buys", 0) or 0),
                sells_h24=int(txns.get("h24", {}).get("sells", 0) or 0),
                price_change_m5=float(price_change.get("m5", 0) or 0),
                price_change_h1=float(price_change.get("h1", 0) or 0),
                price_change_h6=float(price_change.get("h6", 0) or 0),
                price_change_h24=float(price_change.get("h24", 0) or 0),
                pair_created_at=created_at,
            )

            # Volume acceleration & buy pressure
            avg_h6 = metrics.volume_h6 / 6 if metrics.volume_h6 > 0 else 1
            volume_acceleration = metrics.volume_h1 / max(avg_h6, 1)
            total_txns_h1 = metrics.buys_h1 + metrics.sells_h1
            buy_pressure = metrics.buys_h1 / total_txns_h1 if total_txns_h1 > 0 else 0.5

            return {
                "token": token.model_dump(),
                "metrics": metrics.model_dump(),
                "volume_acceleration": volume_acceleration,
                "buy_pressure": buy_pressure,
                "pair_created_at": created_at,
                "source": "dexscreener",
            }
        except Exception as exc:
            logger.debug("Failed to parse pair: %s", exc)
            return None

    def _passes_filters(self, data: dict) -> bool:
        """Check if token passes basic filters before scoring."""
        metrics = data["metrics"]
        if metrics["liquidity_usd"] < settings.MIN_LIQUIDITY_USD:
            return False
        total_txns = metrics["buys_h1"] + metrics["sells_h1"]
        if total_txns < settings.MIN_TRANSACTIONS_1H:
            return False
        created = data.get("pair_created_at")
        if created:
            now = datetime.now(timezone.utc)
            age_minutes = (now - created).total_seconds() / 60
            if age_minutes < settings.MIN_AGE_MINUTES:
                return False
            if age_minutes > settings.MAX_AGE_HOURS * 60:
                return False
        return True
