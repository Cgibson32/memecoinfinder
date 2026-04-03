"""Composite scoring engine — weighted signal aggregation with gates and modifiers."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from config.settings import settings
from models.alert import AlertTier
from models.token import MarketContext, MetricsSnapshot, TokenScore
from storage.database import Database

logger = logging.getLogger(__name__)

# Scam name patterns
SCAM_PATTERNS = [
    re.compile(r"\belon\b", re.IGNORECASE),
    re.compile(r"\bsafe\b.*\bmoon\b", re.IGNORECASE),
    re.compile(r"\brug\b", re.IGNORECASE),
    re.compile(r"\bscam\b", re.IGNORECASE),
]


class CompositeScorer:
    """Combine all sub-scores into a single composite score (0-100)."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def score(
        self,
        token_id: int,
        contract_address: str,
        chain: str,
        token_name: str,
        token_symbol: str,
        metrics: MetricsSnapshot,
        safety_result: dict[str, Any] | None,
        volume_analysis: dict[str, Any] | None,
        holder_analysis: dict[str, Any] | None,
        social_data: dict[str, Any] | None,
        smart_money_data: dict[str, Any] | None,
        narrative_data: dict[str, Any] | None,
        credibility_data: dict[str, Any] | None,
        market_context: MarketContext | None = None,
    ) -> TokenScore:
        """Calculate the final composite score."""
        ts = TokenScore(
            contract_address=contract_address,
            chain=chain,
            token_symbol=token_symbol,
        )

        # --- GATE 1: Safety check ---
        if not safety_result:
            # No safety data available (GoPlus didn't return data) — use neutral score
            safety_score = 50.0
            logger.debug("No safety data for %s — using neutral score", contract_address[:12])
        else:
            safety_score = safety_result.get("safety_score", 0)
            if safety_score == 0:
                ts.rejected = True
                ts.rejection_reason = safety_result.get("rejection_reason", "Failed safety check")
                return ts

        # --- GATE 2: Deployer rejection ---
        if credibility_data and credibility_data.get("deployer_rejected"):
            ts.rejected = True
            ts.rejection_reason = "Serial rug-pull deployer"
            return ts

        # --- GATE 3: Basic filters ---
        if metrics.liquidity_usd < settings.MIN_LIQUIDITY_USD:
            ts.rejected = True
            ts.rejection_reason = f"Liquidity too low: ${metrics.liquidity_usd:.0f}"
            return ts

        total_txns = metrics.buys_h1 + metrics.sells_h1
        if total_txns < settings.MIN_TRANSACTIONS_1H:
            ts.rejected = True
            ts.rejection_reason = f"Too few transactions: {total_txns}"
            return ts

        # --- Sub-scores (each 0-100) ---
        ts.safety_score = safety_score
        ts.volume_score = (volume_analysis or {}).get("volume_score", 0)
        ts.holder_score = (holder_analysis or {}).get("holder_score", 0)
        ts.liquidity_score = self._calc_liquidity_score(metrics)
        ts.smart_money_score = (smart_money_data or {}).get("smart_money_score", 0)
        ts.narrative_score = (narrative_data or {}).get("narrative_score", 0)
        ts.credibility_score = self._calc_credibility_score(credibility_data)
        ts.social_score = self._calc_social_score(social_data)

        # --- Weighted sum ---
        w = settings.weights_dict
        weighted = (
            ts.volume_score * w["volume"]
            + ts.social_score * w["social"]
            + ts.holder_score * w["holder"]
            + ts.safety_score * w["safety"]
            + ts.liquidity_score * w["liquidity"]
            + ts.smart_money_score * w["smart_money"]
            + ts.narrative_score * w["narrative"]
            + ts.credibility_score * w["credibility"]
        )

        # --- Market context adjustment ---
        threshold_adj = 0
        if market_context:
            if market_context.fear_greed_index < 20:
                threshold_adj = 10  # Raise bar in extreme fear
            elif market_context.fear_greed_index > 80:
                threshold_adj = -5  # Lower bar in euphoria

        # --- Bonuses ---
        bonuses: list[str] = []
        bonus_pts = 0.0

        # Dual-source trending
        if credibility_data and credibility_data.get("dual_source"):
            bonus_pts += 5
            bonuses.append("Dual-source trending (+5)")

        # Volume + social correlation
        if (ts.volume_score > 60 and ts.social_score > 50):
            bonus_pts += 10
            bonuses.append("Volume-social correlation (+10)")

        # Google Trends spike
        if social_data and social_data.get("google_trends_spike"):
            bonus_pts += 10
            bonuses.append("Google Trends spike (+10)")

        # Smart social signal (high upvotes, low comments = organic)
        if social_data and social_data.get("organic_signal"):
            bonus_pts += 5
            bonuses.append("Organic social signal (+5)")

        # Score momentum
        score_velocity = await self._calc_score_velocity(token_id, weighted)
        ts.score_velocity = score_velocity
        if score_velocity > 20:
            bonus_pts += 5
            bonuses.append(f"Rising fast ({score_velocity:.0f}pts/hr) (+5)")

        # --- Penalties ---
        penalties: list[str] = []
        penalty_pts = 0.0

        # Scam name patterns
        full_name = f"{token_name} {token_symbol}"
        for pattern in SCAM_PATTERNS:
            if pattern.search(full_name):
                penalty_pts += 10
                penalties.append("Scam name pattern (-10)")
                break

        # Extreme holder concentration
        top10_pct = (holder_analysis or {}).get("top10_concentration", 0)
        if top10_pct > 60:
            penalty_pts += 15
            penalties.append(f"Top10 hold {top10_pct:.0f}% (-15)")
        elif top10_pct > settings.MAX_TOP10_HOLDER_PERCENT:
            penalty_pts += 10
            penalties.append(f"Top10 hold {top10_pct:.0f}% (-10)")

        # Wash trading
        wash = (volume_analysis or {}).get("wash_trading", {})
        if wash.get("is_suspicious"):
            wash_pen = wash.get("penalty", 20)
            penalty_pts += wash_pen
            penalties.append(f"Wash trading detected (-{wash_pen:.0f})")

        # Unlocked LP on new token
        if not (safety_result or {}).get("lp_locked") and metrics.pair_created_at:
            age_hours = (datetime.now(timezone.utc) - metrics.pair_created_at).total_seconds() / 3600
            if age_hours < 24:
                penalty_pts += 10
                penalties.append("Unlocked LP <24h (-10)")

        # Heavy sniping
        if credibility_data and credibility_data.get("sniper_penalty", 0) > 0:
            sp = credibility_data["sniper_penalty"]
            penalty_pts += sp
            penalties.append(f"Sniper bot activity (-{sp:.0f})")

        # Insider wallets
        if credibility_data and credibility_data.get("insider_penalty", 0) > 0:
            ip = credibility_data["insider_penalty"]
            penalty_pts += ip
            penalties.append(f"Insider wallets detected (-{ip:.0f})")

        # --- Final score ---
        ts.bonus_points = bonus_pts
        ts.penalty_points = penalty_pts
        ts.bonuses = bonuses
        ts.penalties = penalties
        ts.composite_score = max(min(weighted + bonus_pts - penalty_pts, 100), 0)

        # Save to database
        await self.db.save_score(
            token_id=token_id,
            composite=ts.composite_score,
            volume=ts.volume_score,
            social=ts.social_score,
            holder=ts.holder_score,
            safety=ts.safety_score,
            liquidity=ts.liquidity_score,
            smart_money=ts.smart_money_score,
            narrative=ts.narrative_score,
            credibility=ts.credibility_score,
            score_velocity=ts.score_velocity,
            bonus=ts.bonus_points,
            penalty=ts.penalty_points,
        )

        return ts

    def _calc_liquidity_score(self, metrics: MetricsSnapshot) -> float:
        """Score liquidity depth (0-100)."""
        liq = metrics.liquidity_usd
        if liq <= 0:
            return 0
        # $5K = 20, $50K = 60, $200K+ = 100
        if liq >= 200_000:
            return 100
        if liq >= 50_000:
            return 60 + (liq - 50_000) / 150_000 * 40
        return min(liq / 50_000 * 60, 60)

    def _calc_social_score(self, social_data: dict[str, Any] | None) -> float:
        """Combine social signals into a single score."""
        if not social_data:
            return 0
        reddit = social_data.get("reddit_score", 0)
        twitter = social_data.get("twitter_score", 0)
        telegram = social_data.get("telegram_score", 0)
        trends = social_data.get("trends_score", 0)

        # Weighted combination (Twitter and Reddit most important)
        combined = (
            twitter * 0.35
            + reddit * 0.30
            + telegram * 0.20
            + trends * 0.15
        )
        return min(combined, 100)

    def _calc_credibility_score(self, credibility_data: dict[str, Any] | None) -> float:
        """Combine deployer, insider, and sniper analysis into credibility score."""
        if not credibility_data:
            return 50  # Unknown = neutral

        deployer_score = credibility_data.get("deployer_score", 50)
        insider_score = credibility_data.get("insider_score", 100)
        sniper_score = credibility_data.get("sniper_score", 100)
        lp_locked = credibility_data.get("lp_locked", False)

        base = (deployer_score * 0.4 + insider_score * 0.3 + sniper_score * 0.3)
        if lp_locked:
            base = min(base + 15, 100)

        return base

    async def _calc_score_velocity(self, token_id: int, current_score: float) -> float:
        """Calculate score change per hour."""
        try:
            async with self.db.db.execute(
                """SELECT composite_score FROM scores
                   WHERE token_id = ?
                     AND scored_at < datetime('now', '-50 minutes')
                   ORDER BY scored_at DESC LIMIT 1""",
                (token_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row and row[0] is not None:
                    return current_score - row[0]
        except Exception:
            pass
        return 0.0

    def get_alert_tier(self, score: float, market_context: MarketContext | None = None) -> AlertTier | None:
        """Determine alert tier based on score and market context."""
        threshold_adj = 0
        if market_context:
            if market_context.fear_greed_index < 20:
                threshold_adj = 10
            elif market_context.fear_greed_index > 80:
                threshold_adj = -5

        if score >= settings.ALERT_CRITICAL_THRESHOLD + threshold_adj:
            return AlertTier.CRITICAL
        if score >= settings.ALERT_WATCH_THRESHOLD + threshold_adj:
            return AlertTier.WATCH
        if score >= settings.ALERT_RADAR_THRESHOLD + threshold_adj:
            return AlertTier.RADAR
        return None
