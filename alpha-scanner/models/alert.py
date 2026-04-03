"""Alert and danger alert models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AlertTier(str, Enum):
    CRITICAL = "CRITICAL"
    WATCH = "WATCH"
    RADAR = "RADAR"


class DangerType(str, Enum):
    SMART_MONEY_EXIT = "SMART_MONEY_EXIT"
    LP_REMOVAL = "LP_REMOVAL"
    HOLDER_DUMP = "HOLDER_DUMP"
    VOLUME_CRASH = "VOLUME_CRASH"
    PRICE_CRASH = "PRICE_CRASH"
    DEPLOYER_MOVE = "DEPLOYER_MOVE"


class Alert(BaseModel):
    contract_address: str
    chain: str
    token_symbol: str = ""
    tier: AlertTier
    composite_score: float
    message_text: str = ""
    sent_at: datetime = Field(default_factory=datetime.utcnow)
    db_id: Optional[int] = None


class DangerAlert(BaseModel):
    contract_address: str
    chain: str
    token_symbol: str = ""
    danger_type: DangerType
    severity: str = "HIGH"  # HIGH, CRITICAL
    details: str = ""
    current_value: float = 0.0
    previous_value: float = 0.0
    change_pct: float = 0.0
    message_text: str = ""
    sent_at: datetime = Field(default_factory=datetime.utcnow)
