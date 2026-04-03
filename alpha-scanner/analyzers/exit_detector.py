"""Exit/danger signal detection for previously-alerted tokens."""

from __future__ import annotations

import logging
from typing import Any

from config.settings import settings
from models.alert import DangerAlert, DangerType
from storage.database import Database

logger = logging.getLogger(__name__)


class ExitDetector:
    """Continuously monitor alerted tokens for danger signals."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def check_all_alerted_tokens(
        self, current_data: dict[str, dict[str, Any]]
    ) -> list[DangerAlert]:
        """Check all previously-alerted tokens for exit signals.

        *current_data*: dict keyed by "chain:address" with current metrics.
        """
        dangers: list[DangerAlert] = []
        alerted_tokens = await self.db.get_alerted_tokens()

        for token in alerted_tokens:
            key = f"{token['chain']}:{token['contract_address']}"
            current = current_data.get(key)
            if not current:
                continue

            token_dangers = self._check_token(token, current)
            dangers.extend(token_dangers)

        if dangers:
            logger.warning("Exit detector found %d danger signals", len(dangers))
        return dangers

    def _check_token(
        self, alert_data: dict, current: dict[str, Any]
    ) -> list[DangerAlert]:
        """Check a single token for all danger conditions."""
        dangers: list[DangerAlert] = []
        addr = alert_data["contract_address"]
        chain = alert_data["chain"]
        symbol = alert_data.get("token_symbol", "")

        # 1. LP removal / significant drop
        alert_lp = alert_data.get("alert_liquidity") or 0
        current_lp = current.get("liquidity_usd", 0)
        if alert_lp > 0 and current_lp > 0:
            lp_change = (current_lp - alert_lp) / alert_lp * 100
            if lp_change <= -settings.EXIT_LP_DROP_PCT:
                dangers.append(DangerAlert(
                    contract_address=addr,
                    chain=chain,
                    token_symbol=symbol,
                    danger_type=DangerType.LP_REMOVAL,
                    severity="CRITICAL" if lp_change <= -50 else "HIGH",
                    details=f"Liquidity dropped {abs(lp_change):.0f}% since alert",
                    current_value=current_lp,
                    previous_value=alert_lp,
                    change_pct=lp_change,
                ))

        # 2. Holder count drop
        alert_holders = alert_data.get("alert_holders") or 0
        current_holders = current.get("holder_count", 0)
        if alert_holders > 0 and current_holders > 0:
            holder_change = (current_holders - alert_holders) / alert_holders * 100
            if holder_change <= -settings.EXIT_HOLDER_DROP_PCT:
                dangers.append(DangerAlert(
                    contract_address=addr,
                    chain=chain,
                    token_symbol=symbol,
                    danger_type=DangerType.HOLDER_DUMP,
                    severity="HIGH",
                    details=f"Holders dropped {abs(holder_change):.0f}% since alert",
                    current_value=current_holders,
                    previous_value=alert_holders,
                    change_pct=holder_change,
                ))

        # 3. Volume crash
        alert_vol = alert_data.get("alert_volume") or 0
        current_vol = current.get("volume_h1", 0)
        if alert_vol > 0:
            vol_ratio = current_vol / alert_vol
            if vol_ratio < settings.EXIT_VOLUME_CRASH_RATIO:
                dangers.append(DangerAlert(
                    contract_address=addr,
                    chain=chain,
                    token_symbol=symbol,
                    danger_type=DangerType.VOLUME_CRASH,
                    severity="HIGH",
                    details=f"Volume crashed to {vol_ratio:.0%} of alert level",
                    current_value=current_vol,
                    previous_value=alert_vol,
                    change_pct=(vol_ratio - 1) * 100,
                ))

        # 4. Price crash
        alert_price = alert_data.get("alert_price") or 0
        current_price = current.get("price_usd", 0)
        if alert_price > 0 and current_price > 0:
            price_change = (current_price - alert_price) / alert_price * 100
            if price_change <= -settings.EXIT_PRICE_DROP_PCT:
                dangers.append(DangerAlert(
                    contract_address=addr,
                    chain=chain,
                    token_symbol=symbol,
                    danger_type=DangerType.PRICE_CRASH,
                    severity="CRITICAL",
                    details=f"Price crashed {abs(price_change):.0f}% since alert",
                    current_value=current_price,
                    previous_value=alert_price,
                    change_pct=price_change,
                ))

        # 5. Smart money selling (check from current data)
        sm_sells = current.get("smart_money_sells", 0)
        if sm_sells > 0:
            dangers.append(DangerAlert(
                contract_address=addr,
                chain=chain,
                token_symbol=symbol,
                danger_type=DangerType.SMART_MONEY_EXIT,
                severity="CRITICAL",
                details=f"{sm_sells} smart money wallet(s) selling",
                current_value=sm_sells,
                previous_value=0,
                change_pct=0,
            ))

        return dangers
