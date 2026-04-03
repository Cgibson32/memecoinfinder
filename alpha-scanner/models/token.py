"""Pydantic data models for tokens, metrics, signals, and scores."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Token(BaseModel):
    contract_address: str
    chain: str
    token_name: str = ""
    token_symbol: str = ""
    pair_address: str = ""
    dex: str = ""
    deployer_address: str = ""
    first_seen: datetime = Field(default_factory=datetime.utcnow)
    db_id: Optional[int] = None


class MetricsSnapshot(BaseModel):
    contract_address: str
    chain: str
    price_usd: float = 0.0
    volume_m5: float = 0.0
    volume_h1: float = 0.0
    volume_h6: float = 0.0
    volume_h24: float = 0.0
    liquidity_usd: float = 0.0
    market_cap: float = 0.0
    fdv: float = 0.0
    holder_count: int = 0
    buys_m5: int = 0
    sells_m5: int = 0
    buys_h1: int = 0
    sells_h1: int = 0
    buys_h6: int = 0
    sells_h6: int = 0
    buys_h24: int = 0
    sells_h24: int = 0
    price_change_m5: float = 0.0
    price_change_h1: float = 0.0
    price_change_h6: float = 0.0
    price_change_h24: float = 0.0
    pair_created_at: Optional[datetime] = None
    recorded_at: datetime = Field(default_factory=datetime.utcnow)


class SafetyResult(BaseModel):
    contract_address: str
    chain: str
    is_honeypot: bool = False
    buy_tax: float = 0.0
    sell_tax: float = 0.0
    is_mintable: bool = False
    can_take_back_ownership: bool = False
    owner_change_balance: bool = False
    hidden_owner: bool = False
    is_proxy: bool = False
    is_open_source: bool = True
    holder_count: int = 0
    lp_locked: bool = False
    lp_lock_duration_days: float = 0.0
    deployer_risk_score: float = 0.0
    safety_score: float = 0.0
    checked_at: datetime = Field(default_factory=datetime.utcnow)


class SocialSignal(BaseModel):
    contract_address: str = ""
    chain: str = ""
    token_symbol: str = ""
    source: str = ""  # reddit, twitter, telegram, google_trends
    mention_count: int = 0
    sentiment_score: float = 0.0  # -1 to 1
    velocity: float = 0.0  # mentions per hour
    upvote_velocity: float = 0.0
    metadata: dict = Field(default_factory=dict)
    recorded_at: datetime = Field(default_factory=datetime.utcnow)


class SmartMoneySignal(BaseModel):
    wallet_address: str
    token_address: str
    chain: str
    action: str = "buy"  # buy or sell
    amount_usd: float = 0.0
    historical_success_rate: float = 0.0
    total_trades: int = 0
    profitable_trades: int = 0


class InsiderInfo(BaseModel):
    contract_address: str
    chain: str
    deployer_address: str = ""
    related_wallets: list[str] = Field(default_factory=list)
    funding_source: str = ""
    insider_holding_pct: float = 0.0
    is_suspicious: bool = False


class MarketContext(BaseModel):
    btc_price: float = 0.0
    btc_change_24h: float = 0.0
    btc_trend: str = "neutral"  # bullish, bearish, neutral
    fear_greed_index: int = 50  # 0-100
    fear_greed_label: str = "Neutral"
    meme_sector_trend: str = "neutral"
    trending_narratives: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TokenScore(BaseModel):
    contract_address: str
    chain: str
    token_symbol: str = ""
    composite_score: float = 0.0
    volume_score: float = 0.0
    social_score: float = 0.0
    holder_score: float = 0.0
    safety_score: float = 0.0
    liquidity_score: float = 0.0
    smart_money_score: float = 0.0
    narrative_score: float = 0.0
    credibility_score: float = 0.0
    score_velocity: float = 0.0  # points per hour change
    bonus_points: float = 0.0
    penalty_points: float = 0.0
    bonuses: list[str] = Field(default_factory=list)
    penalties: list[str] = Field(default_factory=list)
    rejected: bool = False
    rejection_reason: str = ""
    scored_at: datetime = Field(default_factory=datetime.utcnow)
