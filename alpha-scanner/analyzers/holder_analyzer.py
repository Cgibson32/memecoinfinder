"""Holder distribution and growth rate analysis."""

from __future__ import annotations

import logging
from typing import Any

from storage.database import Database

logger = logging.getLogger(__name__)


class HolderAnalyzer:
    """Analyze holder count growth, distribution, and concentration."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def analyze(
        self,
        token_id: int,
        current_holders: int,
        top10_pct: float = 0.0,
    ) -> dict[str, Any]:
        """Return holder analysis with score (0-100)."""
        # Get historical holder data for growth rate
        growth_rate = await self._calc_growth_rate(token_id, current_holders)

        # Growth component: new holders per hour (up to 40 pts)
        growth_score = min(growth_rate / 50, 1.0) * 40

        # Count component: total holder count (up to 30 pts)
        count_score = min(current_holders / 500, 1.0) * 30

        # Distribution component: inverse of top10 concentration (up to 30 pts)
        if top10_pct > 0:
            distribution_score = max(1 - top10_pct / 100, 0) * 30
        else:
            distribution_score = 15  # Unknown distribution gets middle score

        total = growth_score + count_score + distribution_score

        return {
            "holder_score": min(total, 100),
            "holder_count": current_holders,
            "growth_rate_per_hour": growth_rate,
            "top10_concentration": top10_pct,
            "growth_component": growth_score,
            "count_component": count_score,
            "distribution_component": distribution_score,
        }

    async def _calc_growth_rate(self, token_id: int, current_holders: int) -> float:
        """Calculate new holders per hour from historical metrics."""
        try:
            # Get the metrics from ~1 hour ago
            async with self.db.db.execute(
                """SELECT holder_count, recorded_at FROM metrics
                   WHERE token_id = ? AND holder_count > 0
                     AND recorded_at < datetime('now', '-50 minutes')
                   ORDER BY recorded_at DESC LIMIT 1""",
                (token_id,),
            ) as cursor:
                row = await cursor.fetchone()

            if not row or row[0] is None:
                return 0.0

            prev_holders = row[0]
            if prev_holders <= 0:
                return 0.0

            growth = current_holders - prev_holders
            return max(growth, 0)
        except Exception:
            return 0.0
