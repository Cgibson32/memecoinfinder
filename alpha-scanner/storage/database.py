"""SQLite database layer using aiosqlite for async access."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_address TEXT NOT NULL,
    chain TEXT NOT NULL,
    token_name TEXT,
    token_symbol TEXT,
    pair_address TEXT,
    dex TEXT,
    deployer_address TEXT DEFAULT '',
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(contract_address, chain)
);

CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER REFERENCES tokens(id),
    composite_score REAL,
    volume_score REAL,
    social_score REAL,
    holder_score REAL,
    safety_score REAL,
    liquidity_score REAL,
    smart_money_score REAL DEFAULT 0,
    narrative_score REAL DEFAULT 0,
    credibility_score REAL DEFAULT 0,
    score_velocity REAL DEFAULT 0,
    bonus_points REAL DEFAULT 0,
    penalty_points REAL DEFAULT 0,
    scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER REFERENCES tokens(id),
    price_usd REAL,
    volume_h1 REAL,
    volume_h6 REAL,
    volume_h24 REAL,
    liquidity_usd REAL,
    market_cap REAL,
    holder_count INTEGER,
    buys_h1 INTEGER,
    sells_h1 INTEGER,
    price_change_h1 REAL,
    price_change_h24 REAL,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS safety_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER REFERENCES tokens(id),
    is_honeypot BOOLEAN,
    buy_tax REAL,
    sell_tax REAL,
    is_mintable BOOLEAN,
    is_open_source BOOLEAN,
    hidden_owner BOOLEAN,
    lp_locked BOOLEAN DEFAULT 0,
    lp_lock_duration_days REAL DEFAULT 0,
    safety_score REAL,
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER REFERENCES tokens(id),
    tier TEXT CHECK(tier IN ('CRITICAL', 'WATCH', 'RADAR')),
    composite_score REAL,
    message_text TEXT,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS danger_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER REFERENCES tokens(id),
    danger_type TEXT,
    severity TEXT DEFAULT 'HIGH',
    details TEXT,
    current_value REAL,
    previous_value REAL,
    change_pct REAL,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS social_mentions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER REFERENCES tokens(id),
    source TEXT CHECK(source IN ('reddit', 'twitter', 'telegram', 'google_trends')),
    mention_count INTEGER DEFAULT 1,
    sentiment_score REAL,
    metadata TEXT,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS smart_money_wallets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_address TEXT NOT NULL,
    chain TEXT NOT NULL,
    total_trades INTEGER DEFAULT 0,
    profitable_trades INTEGER DEFAULT 0,
    success_rate REAL DEFAULT 0.0,
    avg_return REAL DEFAULT 0.0,
    last_active_at TIMESTAMP,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(wallet_address, chain)
);

CREATE TABLE IF NOT EXISTS smart_money_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_id INTEGER REFERENCES smart_money_wallets(id),
    token_id INTEGER REFERENCES tokens(id),
    action TEXT CHECK(action IN ('buy', 'sell')),
    amount_usd REAL,
    token_price_at_trade REAL,
    traded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS deployer_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deployer_address TEXT NOT NULL,
    token_id INTEGER REFERENCES tokens(id),
    chain TEXT NOT NULL,
    outcome TEXT CHECK(outcome IN ('active', 'rugged', 'dead', 'success', 'unknown')) DEFAULT 'unknown',
    lp_removed_within_24h BOOLEAN DEFAULT 0,
    deployed_at TIMESTAMP,
    assessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS narratives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    keywords TEXT NOT NULL,
    heat_score REAL DEFAULT 0.0,
    source TEXT,
    first_detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS insider_clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id INTEGER REFERENCES tokens(id),
    deployer_address TEXT,
    related_wallets TEXT,
    funding_source TEXT,
    insider_holding_pct REAL DEFAULT 0,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS backtest_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER REFERENCES alerts(id),
    token_id INTEGER REFERENCES tokens(id),
    alert_price REAL,
    price_after_4h REAL,
    price_after_24h REAL,
    return_4h_pct REAL,
    return_24h_pct REAL,
    is_hit_4h BOOLEAN DEFAULT 0,
    is_hit_24h BOOLEAN DEFAULT 0,
    assessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_tokens_chain ON tokens(chain);
CREATE INDEX IF NOT EXISTS idx_tokens_address ON tokens(contract_address);
CREATE INDEX IF NOT EXISTS idx_scores_token ON scores(token_id);
CREATE INDEX IF NOT EXISTS idx_scores_time ON scores(scored_at);
CREATE INDEX IF NOT EXISTS idx_metrics_token ON metrics(token_id);
CREATE INDEX IF NOT EXISTS idx_metrics_time ON metrics(recorded_at);
CREATE INDEX IF NOT EXISTS idx_alerts_token ON alerts(token_id);
CREATE INDEX IF NOT EXISTS idx_alerts_tier ON alerts(tier);
CREATE INDEX IF NOT EXISTS idx_alerts_time ON alerts(sent_at);
CREATE INDEX IF NOT EXISTS idx_danger_alerts_token ON danger_alerts(token_id);
CREATE INDEX IF NOT EXISTS idx_social_token ON social_mentions(token_id);
CREATE INDEX IF NOT EXISTS idx_smart_wallets_addr ON smart_money_wallets(wallet_address);
CREATE INDEX IF NOT EXISTS idx_deployer_addr ON deployer_history(deployer_address);
"""


class Database:
    """Async SQLite database for all AlphaScanner data."""

    def __init__(self, db_path: str = "alpha_scanner.db") -> None:
        self._db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        logger.info("Database connected: %s", self._db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "Database not connected"
        return self._db

    # ---- Token CRUD ----

    async def upsert_token(
        self,
        contract_address: str,
        chain: str,
        token_name: str = "",
        token_symbol: str = "",
        pair_address: str = "",
        dex: str = "",
        deployer_address: str = "",
    ) -> int:
        """Insert or update a token. Returns the token row id."""
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            """INSERT INTO tokens (contract_address, chain, token_name, token_symbol,
                   pair_address, dex, deployer_address, first_seen_at, last_updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(contract_address, chain) DO UPDATE SET
                   token_name = COALESCE(NULLIF(excluded.token_name, ''), tokens.token_name),
                   token_symbol = COALESCE(NULLIF(excluded.token_symbol, ''), tokens.token_symbol),
                   pair_address = COALESCE(NULLIF(excluded.pair_address, ''), tokens.pair_address),
                   dex = COALESCE(NULLIF(excluded.dex, ''), tokens.dex),
                   deployer_address = COALESCE(NULLIF(excluded.deployer_address, ''), tokens.deployer_address),
                   last_updated_at = excluded.last_updated_at""",
            (contract_address, chain, token_name, token_symbol, pair_address, dex, deployer_address, now, now),
        )
        await self.db.commit()
        async with self.db.execute(
            "SELECT id FROM tokens WHERE contract_address = ? AND chain = ?",
            (contract_address, chain),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0]  # type: ignore[index]

    async def get_token_id(self, contract_address: str, chain: str) -> Optional[int]:
        async with self.db.execute(
            "SELECT id FROM tokens WHERE contract_address = ? AND chain = ?",
            (contract_address, chain),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None  # type: ignore[index]

    async def get_token(self, contract_address: str, chain: str) -> Optional[dict]:
        async with self.db.execute(
            "SELECT * FROM tokens WHERE contract_address = ? AND chain = ?",
            (contract_address, chain),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_active_tokens(self, max_age_hours: int = 72) -> list[dict]:
        async with self.db.execute(
            """SELECT * FROM tokens
               WHERE last_updated_at > datetime('now', ? || ' hours')
               ORDER BY last_updated_at DESC""",
            (f"-{max_age_hours}",),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def get_top_tokens(self, limit: int = 50) -> list[dict]:
        async with self.db.execute(
            """SELECT t.*, s.composite_score, s.volume_score, s.social_score,
                      s.holder_score, s.safety_score, s.liquidity_score,
                      s.smart_money_score, s.narrative_score, s.credibility_score,
                      s.score_velocity, s.scored_at
               FROM tokens t
               JOIN scores s ON s.token_id = t.id
               WHERE s.id = (SELECT MAX(id) FROM scores WHERE token_id = t.id)
               ORDER BY s.composite_score DESC
               LIMIT ?""",
            (limit,),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    # ---- Scores ----

    async def save_score(
        self,
        token_id: int,
        composite: float,
        volume: float,
        social: float,
        holder: float,
        safety: float,
        liquidity: float,
        smart_money: float = 0,
        narrative: float = 0,
        credibility: float = 0,
        score_velocity: float = 0,
        bonus: float = 0,
        penalty: float = 0,
    ) -> None:
        await self.db.execute(
            """INSERT INTO scores (token_id, composite_score, volume_score, social_score,
                   holder_score, safety_score, liquidity_score, smart_money_score,
                   narrative_score, credibility_score, score_velocity, bonus_points, penalty_points)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (token_id, composite, volume, social, holder, safety, liquidity,
             smart_money, narrative, credibility, score_velocity, bonus, penalty),
        )
        await self.db.commit()

    async def get_latest_score(self, token_id: int) -> Optional[dict]:
        async with self.db.execute(
            "SELECT * FROM scores WHERE token_id = ? ORDER BY scored_at DESC LIMIT 1",
            (token_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_score_history(self, token_id: int, limit: int = 100) -> list[dict]:
        async with self.db.execute(
            "SELECT * FROM scores WHERE token_id = ? ORDER BY scored_at DESC LIMIT ?",
            (token_id, limit),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    # ---- Metrics ----

    async def save_metrics(
        self,
        token_id: int,
        price_usd: float = 0,
        volume_h1: float = 0,
        volume_h6: float = 0,
        volume_h24: float = 0,
        liquidity_usd: float = 0,
        market_cap: float = 0,
        holder_count: int = 0,
        buys_h1: int = 0,
        sells_h1: int = 0,
        price_change_h1: float = 0,
        price_change_h24: float = 0,
    ) -> None:
        await self.db.execute(
            """INSERT INTO metrics (token_id, price_usd, volume_h1, volume_h6, volume_h24,
                   liquidity_usd, market_cap, holder_count, buys_h1, sells_h1,
                   price_change_h1, price_change_h24)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (token_id, price_usd, volume_h1, volume_h6, volume_h24, liquidity_usd,
             market_cap, holder_count, buys_h1, sells_h1, price_change_h1, price_change_h24),
        )
        await self.db.commit()

    async def get_latest_metrics(self, token_id: int) -> Optional[dict]:
        async with self.db.execute(
            "SELECT * FROM metrics WHERE token_id = ? ORDER BY recorded_at DESC LIMIT 1",
            (token_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    # ---- Safety ----

    async def save_safety_check(
        self,
        token_id: int,
        is_honeypot: bool = False,
        buy_tax: float = 0,
        sell_tax: float = 0,
        is_mintable: bool = False,
        is_open_source: bool = True,
        hidden_owner: bool = False,
        lp_locked: bool = False,
        lp_lock_duration_days: float = 0,
        safety_score: float = 0,
    ) -> None:
        await self.db.execute(
            """INSERT INTO safety_checks (token_id, is_honeypot, buy_tax, sell_tax,
                   is_mintable, is_open_source, hidden_owner, lp_locked,
                   lp_lock_duration_days, safety_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (token_id, is_honeypot, buy_tax, sell_tax, is_mintable, is_open_source,
             hidden_owner, lp_locked, lp_lock_duration_days, safety_score),
        )
        await self.db.commit()

    async def get_latest_safety(self, token_id: int) -> Optional[dict]:
        async with self.db.execute(
            "SELECT * FROM safety_checks WHERE token_id = ? ORDER BY checked_at DESC LIMIT 1",
            (token_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    # ---- Alerts ----

    async def log_alert(
        self, token_id: int, tier: str, composite_score: float, message_text: str = ""
    ) -> int:
        cursor = await self.db.execute(
            "INSERT INTO alerts (token_id, tier, composite_score, message_text) VALUES (?, ?, ?, ?)",
            (token_id, tier, composite_score, message_text),
        )
        await self.db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def log_danger_alert(
        self,
        token_id: int,
        danger_type: str,
        severity: str,
        details: str,
        current_value: float,
        previous_value: float,
        change_pct: float,
    ) -> None:
        await self.db.execute(
            """INSERT INTO danger_alerts (token_id, danger_type, severity, details,
                   current_value, previous_value, change_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (token_id, danger_type, severity, details, current_value, previous_value, change_pct),
        )
        await self.db.commit()

    async def get_recent_alerts(self, hours: int = 24, tier: Optional[str] = None) -> list[dict]:
        query = """SELECT a.*, t.token_symbol, t.chain, t.contract_address
                   FROM alerts a JOIN tokens t ON a.token_id = t.id
                   WHERE a.sent_at > datetime('now', ? || ' hours')"""
        params: list[Any] = [f"-{hours}"]
        if tier:
            query += " AND a.tier = ?"
            params.append(tier)
        query += " ORDER BY a.sent_at DESC"
        async with self.db.execute(query, params) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def get_alert_stats(self, hours: int = 24) -> dict[str, int]:
        async with self.db.execute(
            """SELECT tier, COUNT(*) as cnt FROM alerts
               WHERE sent_at > datetime('now', ? || ' hours')
               GROUP BY tier""",
            (f"-{hours}",),
        ) as cursor:
            rows = await cursor.fetchall()
            return {row["tier"]: row["cnt"] for row in rows}

    async def get_alerted_tokens(self) -> list[dict]:
        """Get tokens that have been alerted (for exit monitoring)."""
        async with self.db.execute(
            """SELECT DISTINCT t.*, a.composite_score as alert_score, a.sent_at as alert_time,
                      m.price_usd as alert_price, m.liquidity_usd as alert_liquidity,
                      m.holder_count as alert_holders, m.volume_h1 as alert_volume
               FROM alerts a
               JOIN tokens t ON a.token_id = t.id
               LEFT JOIN metrics m ON m.token_id = t.id
                   AND m.id = (SELECT MAX(id) FROM metrics WHERE token_id = t.id
                               AND recorded_at <= a.sent_at)
               WHERE a.tier IN ('CRITICAL', 'WATCH')
                 AND a.sent_at > datetime('now', '-72 hours')
               ORDER BY a.sent_at DESC""",
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    # ---- Social ----

    async def save_social_mention(
        self,
        token_id: int,
        source: str,
        mention_count: int = 1,
        sentiment_score: float = 0,
        metadata: Optional[dict] = None,
    ) -> None:
        await self.db.execute(
            "INSERT INTO social_mentions (token_id, source, mention_count, sentiment_score, metadata) VALUES (?, ?, ?, ?, ?)",
            (token_id, source, mention_count, sentiment_score, json.dumps(metadata or {})),
        )
        await self.db.commit()

    async def get_social_velocity(self, token_id: int, source: str, hours: int = 1) -> float:
        async with self.db.execute(
            """SELECT SUM(mention_count) as total FROM social_mentions
               WHERE token_id = ? AND source = ?
                 AND recorded_at > datetime('now', ? || ' hours')""",
            (token_id, source, f"-{hours}"),
        ) as cursor:
            row = await cursor.fetchone()
            return float(row[0]) if row and row[0] else 0.0  # type: ignore[index]

    # ---- Smart Money ----

    async def upsert_smart_wallet(
        self, wallet_address: str, chain: str, total_trades: int = 0,
        profitable_trades: int = 0, success_rate: float = 0, avg_return: float = 0,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            """INSERT INTO smart_money_wallets (wallet_address, chain, total_trades,
                   profitable_trades, success_rate, avg_return, last_active_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(wallet_address, chain) DO UPDATE SET
                   total_trades = excluded.total_trades,
                   profitable_trades = excluded.profitable_trades,
                   success_rate = excluded.success_rate,
                   avg_return = excluded.avg_return,
                   last_active_at = excluded.last_active_at""",
            (wallet_address, chain, total_trades, profitable_trades, success_rate, avg_return, now),
        )
        await self.db.commit()
        async with self.db.execute(
            "SELECT id FROM smart_money_wallets WHERE wallet_address = ? AND chain = ?",
            (wallet_address, chain),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0]  # type: ignore[index]

    async def get_smart_wallets(self, chain: Optional[str] = None) -> list[dict]:
        if chain:
            query = "SELECT * FROM smart_money_wallets WHERE chain = ? ORDER BY success_rate DESC"
            params: tuple = (chain,)
        else:
            query = "SELECT * FROM smart_money_wallets ORDER BY success_rate DESC"
            params = ()
        async with self.db.execute(query, params) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def save_smart_money_trade(
        self, wallet_id: int, token_id: int, action: str, amount_usd: float, price: float
    ) -> None:
        await self.db.execute(
            "INSERT INTO smart_money_trades (wallet_id, token_id, action, amount_usd, token_price_at_trade) VALUES (?, ?, ?, ?, ?)",
            (wallet_id, token_id, action, amount_usd, price),
        )
        await self.db.commit()

    async def get_smart_money_activity(self, token_id: int) -> list[dict]:
        async with self.db.execute(
            """SELECT t.*, w.wallet_address, w.success_rate, w.total_trades
               FROM smart_money_trades t
               JOIN smart_money_wallets w ON t.wallet_id = w.id
               WHERE t.token_id = ?
               ORDER BY t.traded_at DESC""",
            (token_id,),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    # ---- Deployer ----

    async def save_deployer_history(
        self, deployer_address: str, token_id: int, chain: str,
        outcome: str = "unknown", lp_removed_within_24h: bool = False,
    ) -> None:
        await self.db.execute(
            """INSERT INTO deployer_history (deployer_address, token_id, chain, outcome, lp_removed_within_24h)
               VALUES (?, ?, ?, ?, ?)""",
            (deployer_address, token_id, chain, outcome, lp_removed_within_24h),
        )
        await self.db.commit()

    async def get_deployer_history(self, deployer_address: str) -> list[dict]:
        async with self.db.execute(
            """SELECT dh.*, t.token_name, t.token_symbol
               FROM deployer_history dh
               LEFT JOIN tokens t ON dh.token_id = t.id
               WHERE dh.deployer_address = ?
               ORDER BY dh.assessed_at DESC""",
            (deployer_address,),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    # ---- Narratives ----

    async def upsert_narrative(
        self, name: str, keywords: list[str], heat_score: float, source: str = ""
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            """INSERT INTO narratives (name, keywords, heat_score, source, last_updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT DO NOTHING""",
            (name, json.dumps(keywords), heat_score, source, now),
        )
        # Update if exists
        await self.db.execute(
            """UPDATE narratives SET heat_score = ?, keywords = ?, last_updated_at = ?
               WHERE name = ?""",
            (heat_score, json.dumps(keywords), now, name),
        )
        await self.db.commit()

    async def get_active_narratives(self, min_heat: float = 0.1) -> list[dict]:
        async with self.db.execute(
            """SELECT * FROM narratives
               WHERE heat_score >= ?
                 AND last_updated_at > datetime('now', '-24 hours')
               ORDER BY heat_score DESC""",
            (min_heat,),
        ) as cursor:
            rows = [dict(r) for r in await cursor.fetchall()]
            for row in rows:
                row["keywords"] = json.loads(row["keywords"])
            return rows

    # ---- Insiders ----

    async def save_insider_cluster(
        self, token_id: int, deployer_address: str, related_wallets: list[str],
        funding_source: str, insider_holding_pct: float,
    ) -> None:
        await self.db.execute(
            """INSERT INTO insider_clusters (token_id, deployer_address, related_wallets,
                   funding_source, insider_holding_pct)
               VALUES (?, ?, ?, ?, ?)""",
            (token_id, deployer_address, json.dumps(related_wallets), funding_source, insider_holding_pct),
        )
        await self.db.commit()

    async def get_insider_cluster(self, token_id: int) -> Optional[dict]:
        async with self.db.execute(
            "SELECT * FROM insider_clusters WHERE token_id = ? ORDER BY detected_at DESC LIMIT 1",
            (token_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                d = dict(row)
                d["related_wallets"] = json.loads(d["related_wallets"])
                return d
            return None

    # ---- Backtest ----

    async def save_backtest_result(
        self, alert_id: int, token_id: int, alert_price: float,
        price_after_4h: float, price_after_24h: float,
    ) -> None:
        ret_4h = ((price_after_4h - alert_price) / alert_price * 100) if alert_price > 0 else 0
        ret_24h = ((price_after_24h - alert_price) / alert_price * 100) if alert_price > 0 else 0
        await self.db.execute(
            """INSERT INTO backtest_results (alert_id, token_id, alert_price,
                   price_after_4h, price_after_24h, return_4h_pct, return_24h_pct,
                   is_hit_4h, is_hit_24h)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (alert_id, token_id, alert_price, price_after_4h, price_after_24h,
             ret_4h, ret_24h, ret_4h >= 50, ret_24h >= 100),
        )
        await self.db.commit()

    async def get_backtest_stats(self) -> dict[str, Any]:
        async with self.db.execute(
            """SELECT COUNT(*) as total,
                      SUM(is_hit_4h) as hits_4h,
                      SUM(is_hit_24h) as hits_24h,
                      AVG(return_4h_pct) as avg_return_4h,
                      AVG(return_24h_pct) as avg_return_24h,
                      MAX(return_24h_pct) as best_return,
                      MIN(return_24h_pct) as worst_return
               FROM backtest_results""",
        ) as cursor:
            row = await cursor.fetchone()
            if not row or row[0] == 0:  # type: ignore[index]
                return {"total": 0, "hit_rate_4h": 0, "hit_rate_24h": 0}
            total = row["total"]
            return {
                "total": total,
                "hits_4h": row["hits_4h"] or 0,
                "hits_24h": row["hits_24h"] or 0,
                "hit_rate_4h": (row["hits_4h"] or 0) / total * 100,
                "hit_rate_24h": (row["hits_24h"] or 0) / total * 100,
                "avg_return_4h": row["avg_return_4h"] or 0,
                "avg_return_24h": row["avg_return_24h"] or 0,
                "best_return": row["best_return"] or 0,
                "worst_return": row["worst_return"] or 0,
            }

    # ---- Cleanup ----

    async def cleanup_old_data(self, hours: int = 168) -> int:
        """Delete data older than *hours*. Returns approximate row count deleted."""
        cutoff = f"-{hours} hours"
        total = 0
        for table, col in [
            ("metrics", "recorded_at"),
            ("scores", "scored_at"),
            ("social_mentions", "recorded_at"),
            ("safety_checks", "checked_at"),
        ]:
            cursor = await self.db.execute(
                f"DELETE FROM {table} WHERE {col} < datetime('now', ?)", (cutoff,)
            )
            total += cursor.rowcount
        await self.db.commit()
        return total

    async def get_db_size(self) -> int:
        """Return database file size in bytes."""
        return Path(self._db_path).stat().st_size if Path(self._db_path).exists() else 0
