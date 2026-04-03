"""Smart money wallet tracking — the highest-alpha signal."""

from __future__ import annotations

import logging
from typing import Any

from storage.database import Database
from storage.cache import TTLCache

logger = logging.getLogger(__name__)


class SmartMoneyAnalyzer:
    """Track historically successful wallets and detect their new buys."""

    def __init__(self, db: Database, cache: TTLCache) -> None:
        self.db = db
        self.cache = cache

    async def analyze(self, token_id: int, chain: str) -> dict[str, Any]:
        """Check if any smart money wallets have traded this token."""
        activity = await self.db.get_smart_money_activity(token_id)

        if not activity:
            return {
                "smart_money_score": 0,
                "smart_money_buys": 0,
                "smart_money_sells": 0,
                "wallets": [],
            }

        buys = [a for a in activity if a["action"] == "buy"]
        sells = [a for a in activity if a["action"] == "sell"]

        # Score based on number of smart money buyers
        buy_count = len(buys)
        if buy_count >= 3:
            score = 85
        elif buy_count >= 2:
            score = 65
        elif buy_count >= 1:
            score = 45
        else:
            score = 0

        # Boost by average success rate of buying wallets
        if buys:
            avg_success = sum(b["success_rate"] for b in buys) / len(buys)
            score = score * (0.5 + avg_success * 0.5)

        # Penalize if smart money is selling
        if sells and buys:
            sell_ratio = len(sells) / (len(buys) + len(sells))
            if sell_ratio > 0.5:
                score *= 0.3  # Heavy penalty — smart money exiting

        return {
            "smart_money_score": min(score, 100),
            "smart_money_buys": len(buys),
            "smart_money_sells": len(sells),
            "wallets": [
                {
                    "address": a["wallet_address"],
                    "action": a["action"],
                    "success_rate": a["success_rate"],
                }
                for a in activity[:5]
            ],
        }

    async def register_trade(
        self,
        wallet_address: str,
        token_id: int,
        chain: str,
        action: str,
        amount_usd: float,
        price: float,
    ) -> None:
        """Record a trade by a known smart money wallet."""
        wallet_id = await self.db.upsert_smart_wallet(wallet_address, chain)
        await self.db.save_smart_money_trade(wallet_id, token_id, action, amount_usd, price)

    async def seed_from_top_traders(
        self, top_traders: list[dict], chain: str
    ) -> int:
        """Seed smart money wallet list from DEXScreener top traders data.

        Call this with traders from tokens that went 10x+.
        """
        added = 0
        for trader in top_traders:
            addr = trader.get("wallet_address", "")
            if not addr:
                continue
            await self.db.upsert_smart_wallet(
                wallet_address=addr,
                chain=chain,
                total_trades=trader.get("total_trades", 0),
                profitable_trades=trader.get("profitable_trades", 0),
                success_rate=trader.get("success_rate", 0),
                avg_return=trader.get("avg_return", 0),
            )
            added += 1

        if added:
            logger.info("Seeded %d smart money wallets for %s", added, chain)
        return added

    async def update_wallet_stats(self, wallet_address: str, chain: str) -> None:
        """Recalculate success rate for a wallet based on trade outcomes."""
        wallets = await self.db.get_smart_wallets(chain)
        for w in wallets:
            if w["wallet_address"] == wallet_address:
                total = w["total_trades"]
                profitable = w["profitable_trades"]
                rate = profitable / total if total > 0 else 0
                await self.db.upsert_smart_wallet(
                    wallet_address=wallet_address,
                    chain=chain,
                    total_trades=total,
                    profitable_trades=profitable,
                    success_rate=rate,
                    avg_return=w.get("avg_return", 0),
                )
                break
