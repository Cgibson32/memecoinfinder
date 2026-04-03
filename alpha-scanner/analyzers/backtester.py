"""Backtesting module — validates scoring accuracy over time."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from collectors.dexscreener import DexScreenerCollector
from storage.database import Database

logger = logging.getLogger(__name__)


class Backtester:
    """Periodically evaluate past CRITICAL alerts to measure hit rate."""

    def __init__(self, db: Database, dex_collector: DexScreenerCollector) -> None:
        self.db = db
        self.dex = dex_collector

    async def run(self) -> dict[str, Any]:
        """Evaluate all un-assessed CRITICAL alerts that are at least 4 hours old."""
        assessed = 0
        hits_4h = 0
        hits_24h = 0

        # Get CRITICAL alerts from 4-48 hours ago that haven't been backtested
        alerts = await self._get_unassessed_alerts()

        for alert in alerts:
            token_id = alert["token_id"]
            alert_id = alert["id"]

            # Get the price at alert time
            alert_metrics = await self._get_metrics_at_alert(token_id, alert["sent_at"])
            if not alert_metrics or not alert_metrics.get("price_usd"):
                continue

            alert_price = alert_metrics["price_usd"]

            # Get current price
            current_metrics = await self.db.get_latest_metrics(token_id)
            if not current_metrics or not current_metrics.get("price_usd"):
                continue

            current_price = current_metrics["price_usd"]

            # For 4h check, use metrics ~4h after alert
            price_4h = await self._get_price_after(token_id, alert["sent_at"], hours=4)
            price_24h = current_price  # Use latest as proxy for 24h

            if price_4h is None:
                price_4h = current_price

            await self.db.save_backtest_result(
                alert_id=alert_id,
                token_id=token_id,
                alert_price=alert_price,
                price_after_4h=price_4h,
                price_after_24h=price_24h,
            )

            ret_4h = (price_4h - alert_price) / alert_price * 100 if alert_price > 0 else 0
            ret_24h = (price_24h - alert_price) / alert_price * 100 if alert_price > 0 else 0

            if ret_4h >= 50:
                hits_4h += 1
            if ret_24h >= 100:
                hits_24h += 1
            assessed += 1

        stats = await self.db.get_backtest_stats()
        logger.info(
            "Backtest: assessed %d alerts, overall hit rate 4h: %.1f%%, 24h: %.1f%%",
            assessed,
            stats.get("hit_rate_4h", 0),
            stats.get("hit_rate_24h", 0),
        )
        return stats

    async def _get_unassessed_alerts(self) -> list[dict]:
        """Get CRITICAL alerts older than 4 hours that haven't been backtested."""
        async with self.db.db.execute(
            """SELECT a.* FROM alerts a
               LEFT JOIN backtest_results br ON br.alert_id = a.id
               WHERE a.tier = 'CRITICAL'
                 AND a.sent_at < datetime('now', '-4 hours')
                 AND a.sent_at > datetime('now', '-48 hours')
                 AND br.id IS NULL
               ORDER BY a.sent_at ASC
               LIMIT 20""",
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def _get_metrics_at_alert(self, token_id: int, alert_time: str) -> dict | None:
        """Get metrics closest to alert time."""
        async with self.db.db.execute(
            """SELECT * FROM metrics
               WHERE token_id = ?
                 AND recorded_at <= ?
               ORDER BY recorded_at DESC LIMIT 1""",
            (token_id, alert_time),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def _get_price_after(
        self, token_id: int, alert_time: str, hours: int
    ) -> float | None:
        """Get price approximately *hours* after alert."""
        async with self.db.db.execute(
            """SELECT price_usd FROM metrics
               WHERE token_id = ?
                 AND recorded_at > datetime(?, '+' || ? || ' hours')
               ORDER BY recorded_at ASC LIMIT 1""",
            (token_id, alert_time, str(hours)),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None  # type: ignore[index]
