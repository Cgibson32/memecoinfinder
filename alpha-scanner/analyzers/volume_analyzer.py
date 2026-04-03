"""Volume pattern analysis with wash trade detection."""

from __future__ import annotations

import logging
from typing import Any

from models.token import MetricsSnapshot

logger = logging.getLogger(__name__)


class VolumeAnalyzer:
    """Analyze volume patterns, buy pressure, sustainability, and wash trading signals."""

    def analyze(self, metrics: MetricsSnapshot, extra_data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return volume analysis with score (0-100) and sub-metrics."""
        extra = extra_data or {}

        # Volume acceleration: how much faster is current volume vs average
        avg_h6 = metrics.volume_h6 / 6 if metrics.volume_h6 > 0 else 1
        volume_acceleration = metrics.volume_h1 / max(avg_h6, 1)

        # Buy pressure: ratio of buy vs sell transactions
        total_h1 = metrics.buys_h1 + metrics.sells_h1
        buy_pressure = metrics.buys_h1 / total_h1 if total_h1 > 0 else 0.5

        # Volume sustainability: is volume growing or just a spike?
        sustainability = 0.0
        if metrics.volume_h6 > 0 and metrics.volume_h24 > 0:
            h1_share = metrics.volume_h1 / (metrics.volume_h24 or 1)
            h6_share = metrics.volume_h6 / (metrics.volume_h24 or 1)
            # If h1 > proportional share AND h6 > proportional share = sustained
            if h1_share > (1 / 24) and h6_share > (6 / 24):
                sustainability = min((h1_share / (1 / 24) + h6_share / (6 / 24)) / 2, 2.0) / 2

        # Whale detection: check if single large transactions
        whale_buy_bonus = 0.0
        if extra.get("volume_acceleration", 0) > 5:
            whale_buy_bonus = 0.5  # Possible whale buying

        # Wash trade detection
        wash_trade_penalty = 0.0
        wash_signals = self._detect_wash_trading(metrics, extra)
        if wash_signals["is_suspicious"]:
            wash_trade_penalty = wash_signals["penalty"]

        # Score formula
        accel_score = min(volume_acceleration / 5, 1.0) * 40
        pressure_score = min(buy_pressure / 0.8, 1.0) * 30
        sustain_score = sustainability * 20
        whale_score = whale_buy_bonus * 10
        raw_score = accel_score + pressure_score + sustain_score + whale_score

        # Apply wash trade penalty
        final_score = max(raw_score - wash_trade_penalty, 0)

        return {
            "volume_score": min(final_score, 100),
            "volume_acceleration": volume_acceleration,
            "buy_pressure": buy_pressure,
            "sustainability": sustainability,
            "whale_detected": whale_buy_bonus > 0,
            "wash_trading": wash_signals,
            "wash_trade_penalty": wash_trade_penalty,
        }

    def _detect_wash_trading(self, metrics: MetricsSnapshot, extra: dict) -> dict[str, Any]:
        """Detect potential wash trading patterns."""
        signals: list[str] = []
        penalty = 0.0

        total_h1 = metrics.buys_h1 + metrics.sells_h1

        # Signal 1: Extremely high buy ratio (>95% buys, nearly 0 sells)
        if total_h1 > 10:
            buy_ratio = metrics.buys_h1 / total_h1
            if buy_ratio > 0.95:
                signals.append("suspicious_buy_ratio")
                penalty += 15

        # Signal 2: Very low transaction count but high volume (few wallets)
        if metrics.volume_h1 > 0 and total_h1 > 0:
            avg_tx_size = metrics.volume_h1 / total_h1
            if avg_tx_size > metrics.liquidity_usd * 0.1 and total_h1 < 30:
                signals.append("large_avg_tx_few_trades")
                penalty += 10

        # Signal 3: Volume disproportionate to liquidity
        if metrics.liquidity_usd > 0:
            vol_liq_ratio = metrics.volume_h1 / metrics.liquidity_usd
            if vol_liq_ratio > 10:
                signals.append("volume_liquidity_mismatch")
                penalty += 10

        # Signal 4: Perfect round numbers in buy/sell counts
        if metrics.buys_h1 > 0 and metrics.sells_h1 > 0:
            if metrics.buys_h1 % 10 == 0 and metrics.sells_h1 % 10 == 0:
                signals.append("round_number_txns")
                penalty += 5

        return {
            "is_suspicious": len(signals) >= 2,
            "signals": signals,
            "penalty": min(penalty, 25),
        }
