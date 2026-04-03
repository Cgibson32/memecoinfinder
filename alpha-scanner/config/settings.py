"""Application settings loaded from environment variables with sensible defaults."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _get_int(key: str, default: int = 0) -> int:
    val = os.getenv(key, "")
    return int(val) if val.strip() else default


def _get_float(key: str, default: float = 0.0) -> float:
    val = os.getenv(key, "")
    return float(val) if val.strip() else default


def _get_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


def _get_list(key: str, default: str = "") -> list[str]:
    raw = os.getenv(key, default)
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


class Settings:
    """Centralised application settings."""

    # --- Telegram (required) ---
    TELEGRAM_BOT_TOKEN: str = _get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = _get("TELEGRAM_CHAT_ID", "")

    # --- Telegram group monitoring (optional – Telethon) ---
    TELEGRAM_API_ID: int = _get_int("TELEGRAM_API_ID", 0)
    TELEGRAM_API_HASH: str = _get("TELEGRAM_API_HASH", "")
    TELEGRAM_ALPHA_GROUPS: list[str] = _get_list("TELEGRAM_ALPHA_GROUPS")

    # --- Twitter / Nitter ---
    NITTER_INSTANCE: str = _get("NITTER_INSTANCE", "https://nitter.net")

    # --- Scoring weights (must sum to 1.0) ---
    WEIGHT_VOLUME: float = _get_float("WEIGHT_VOLUME", 0.15)
    WEIGHT_SOCIAL: float = _get_float("WEIGHT_SOCIAL", 0.12)
    WEIGHT_HOLDER: float = _get_float("WEIGHT_HOLDER", 0.12)
    WEIGHT_SAFETY: float = _get_float("WEIGHT_SAFETY", 0.13)
    WEIGHT_LIQUIDITY: float = _get_float("WEIGHT_LIQUIDITY", 0.08)
    WEIGHT_SMART_MONEY: float = _get_float("WEIGHT_SMART_MONEY", 0.15)
    WEIGHT_NARRATIVE: float = _get_float("WEIGHT_NARRATIVE", 0.10)
    WEIGHT_CREDIBILITY: float = _get_float("WEIGHT_CREDIBILITY", 0.15)

    # --- Alert thresholds ---
    ALERT_CRITICAL_THRESHOLD: int = 80
    ALERT_WATCH_THRESHOLD: int = 60
    ALERT_RADAR_THRESHOLD: int = 40

    # --- Filter thresholds ---
    MIN_LIQUIDITY_USD: float = _get_float("MIN_LIQUIDITY_USD", 5000)
    MIN_AGE_MINUTES: int = _get_int("MIN_AGE_MINUTES", 5)
    MAX_AGE_HOURS: int = _get_int("MAX_AGE_HOURS", 72)
    MIN_TRANSACTIONS_1H: int = _get_int("MIN_TRANSACTIONS_1H", 20)
    MAX_SELL_TAX_PERCENT: float = _get_float("MAX_SELL_TAX_PERCENT", 10)
    MAX_BUY_TAX_PERCENT: float = _get_float("MAX_BUY_TAX_PERCENT", 10)
    MAX_TOP10_HOLDER_PERCENT: float = _get_float("MAX_TOP10_HOLDER_PERCENT", 50)

    # --- Exit / danger alert thresholds ---
    EXIT_HOLDER_DROP_PCT: float = _get_float("EXIT_HOLDER_DROP_PCT", 10)
    EXIT_LP_DROP_PCT: float = _get_float("EXIT_LP_DROP_PCT", 20)
    EXIT_VOLUME_CRASH_RATIO: float = _get_float("EXIT_VOLUME_CRASH_RATIO", 0.2)
    EXIT_PRICE_DROP_PCT: float = _get_float("EXIT_PRICE_DROP_PCT", 50)

    # --- Polling intervals (seconds) ---
    DEXSCREENER_POLL_INTERVAL: int = 30
    GECKOTERMINAL_POLL_INTERVAL: int = 60
    REDDIT_POLL_INTERVAL: int = 120
    TWITTER_POLL_INTERVAL: int = 120
    GOOGLE_TRENDS_POLL_INTERVAL: int = 900
    MARKET_CONTEXT_POLL_INTERVAL: int = 300
    SCORING_INTERVAL: int = 30
    EXIT_CHECK_INTERVAL: int = 60
    CLEANUP_INTERVAL: int = 3600
    BACKTEST_INTERVAL: int = 21600  # 6 hours
    HEALTH_CHECK_INTERVAL: int = 300
    HEARTBEAT_INTERVAL: int = 1800

    # --- Rate limits (requests per minute) ---
    DEXSCREENER_RATE_LIMIT: int = 280
    GOPLUS_RATE_LIMIT: int = 28
    GECKOTERMINAL_RATE_LIMIT: int = 25
    REDDIT_RATE_LIMIT: int = 20
    COINGECKO_RATE_LIMIT: int = 25

    # --- Chains to monitor ---
    ACTIVE_CHAINS: list[str] = _get_list("ACTIVE_CHAINS", "solana,ethereum,base,bsc")

    # --- Database ---
    DB_PATH: str = _get("DB_PATH", "alpha_scanner.db")

    # --- Logging ---
    LOG_LEVEL: str = _get("LOG_LEVEL", "INFO")
    LOG_FILE: str = _get("LOG_FILE", "alpha_scanner.log")
    LOG_MAX_BYTES: int = 10 * 1024 * 1024  # 10 MB
    LOG_BACKUP_COUNT: int = 5

    @property
    def telegram_monitoring_enabled(self) -> bool:
        return bool(self.TELEGRAM_API_ID and self.TELEGRAM_API_HASH)

    @property
    def weights_dict(self) -> dict[str, float]:
        return {
            "volume": self.WEIGHT_VOLUME,
            "social": self.WEIGHT_SOCIAL,
            "holder": self.WEIGHT_HOLDER,
            "safety": self.WEIGHT_SAFETY,
            "liquidity": self.WEIGHT_LIQUIDITY,
            "smart_money": self.WEIGHT_SMART_MONEY,
            "narrative": self.WEIGHT_NARRATIVE,
            "credibility": self.WEIGHT_CREDIBILITY,
        }


settings = Settings()
