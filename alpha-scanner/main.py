#!/usr/bin/env python3
"""AlphaScanner — Autonomous meme coin research agent.

Usage:
    python main.py              # Normal mode
    python main.py --dry-run    # Print alerts to console instead of Telegram
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp

from alerts.telegram_bot import TelegramAlertBot
from analyzers.backtester import Backtester
from analyzers.deployer_analyzer import DeployerAnalyzer
from analyzers.exit_detector import ExitDetector
from analyzers.holder_analyzer import HolderAnalyzer
from analyzers.insider_detector import InsiderDetector
from analyzers.narrative_analyzer import NarrativeAnalyzer
from analyzers.safety import SafetyAnalyzer
from analyzers.scorer import CompositeScorer
from analyzers.smart_money import SmartMoneyAnalyzer
from analyzers.sniper_detector import SniperDetector
from analyzers.volume_analyzer import VolumeAnalyzer
from collectors.dexscreener import DexScreenerCollector
from collectors.geckoterminal import GeckoTerminalCollector
from collectors.google_trends import GoogleTrendsCollector
from collectors.market_context import MarketContextCollector
from collectors.reddit import RedditCollector
from collectors.telegram_monitor import TelegramMonitorCollector
from collectors.twitter import TwitterCollector
from config.settings import settings
from models.token import MarketContext, MetricsSnapshot, TokenScore
from storage.cache import TTLCache
from storage.database import Database
from utils.rate_limiter import RateLimiter

logger = logging.getLogger("alpha_scanner")

BANNER = r"""
    _    _       _           ____
   / \  | |_ __ | |__   __ _/ ___|  ___ __ _ _ __  _ __   ___ _ __
  / _ \ | | '_ \| '_ \ / _` \___ \ / __/ _` | '_ \| '_ \ / _ \ '__|
 / ___ \| | |_) | | | | (_| |___) | (_| (_| | | | | | | |  __/ |
/_/   \_\_| .__/|_| |_|\__,_|____/ \___\__,_|_| |_|_| |_|\___|_|
          |_|
                    Meme Coin Research Agent v1.0
"""


def setup_logging(dry_run: bool = False) -> None:
    """Configure structured logging to console and rotating file."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # File handler
    if not dry_run:
        fh = logging.handlers.RotatingFileHandler(
            settings.LOG_FILE,
            maxBytes=settings.LOG_MAX_BYTES,
            backupCount=settings.LOG_BACKUP_COUNT,
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)


class AlphaScanner:
    """Main orchestrator — starts all collectors, analyzers, and alert loops."""

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.start_time = time.monotonic()
        self._running = True

        # Shared resources
        self.session: aiohttp.ClientSession | None = None
        self.db = Database(settings.DB_PATH)
        self.cache = TTLCache()

        # Rate limiters
        self.dex_limiter = RateLimiter(settings.DEXSCREENER_RATE_LIMIT)
        self.goplus_limiter = RateLimiter(settings.GOPLUS_RATE_LIMIT)
        self.gecko_limiter = RateLimiter(settings.GECKOTERMINAL_RATE_LIMIT)
        self.reddit_limiter = RateLimiter(settings.REDDIT_RATE_LIMIT)
        self.cg_limiter = RateLimiter(settings.COINGECKO_RATE_LIMIT)

        # Components (initialized in start())
        self.bot: TelegramAlertBot | None = None
        self.dex_collector: DexScreenerCollector | None = None
        self.gecko_collector: GeckoTerminalCollector | None = None
        self.reddit_collector: RedditCollector | None = None
        self.twitter_collector: TwitterCollector | None = None
        self.telegram_collector: TelegramMonitorCollector | None = None
        self.trends_collector: GoogleTrendsCollector | None = None
        self.market_collector: MarketContextCollector | None = None
        self.safety_analyzer: SafetyAnalyzer | None = None
        self.volume_analyzer = VolumeAnalyzer()
        self.holder_analyzer: HolderAnalyzer | None = None
        self.smart_money: SmartMoneyAnalyzer | None = None
        self.deployer_analyzer: DeployerAnalyzer | None = None
        self.narrative_analyzer: NarrativeAnalyzer | None = None
        self.insider_detector: InsiderDetector | None = None
        self.sniper_detector: SniperDetector | None = None
        self.exit_detector: ExitDetector | None = None
        self.backtester: Backtester | None = None
        self.scorer: CompositeScorer | None = None

        # Token queue for discovered tokens
        self.token_queue: asyncio.Queue[dict] = asyncio.Queue()

        # Market context
        self.market_context = MarketContext()

    async def start(self) -> None:
        """Initialize all components and enter the main loop."""
        print(BANNER)
        print(f"  Mode: {'DRY RUN' if self.dry_run else 'LIVE'}")
        print(f"  Chains: {', '.join(settings.ACTIVE_CHAINS)}")
        print(f"  Database: {settings.DB_PATH}")
        print()

        # Initialize database
        await self.db.connect()
        logger.info("Database initialized")

        # Initialize HTTP session
        self.session = aiohttp.ClientSession(
            headers={"User-Agent": "AlphaScanner/1.0"},
            timeout=aiohttp.ClientTimeout(total=30),
        )

        # Validate required services
        if not await self._validate_startup():
            logger.error("Startup validation failed. Exiting.")
            await self.shutdown()
            return

        # Initialize components
        self._init_components()

        # Initialize narrative seed data
        if self.narrative_analyzer:
            await self.narrative_analyzer.initialize()

        # Initialize Telegram bot
        self.bot = TelegramAlertBot(self.db, self.cache, dry_run=self.dry_run)
        await self.bot.initialize()

        # Start Telethon listener if configured
        if self.telegram_collector:
            await self.telegram_collector.start_listening()

        # Send startup message
        chain_count = len(settings.ACTIVE_CHAINS)
        await self.bot.send_message(
            f"🟢 AlphaScanner online\\. Monitoring {chain_count} chains\\.\n"
            f"Mode: {'DRY RUN' if self.dry_run else 'LIVE'}",
        )

        logger.info("AlphaScanner started — entering main loop")

        # Launch all tasks
        tasks = [
            asyncio.create_task(self._collector_loop("dexscreener", self.dex_collector, settings.DEXSCREENER_POLL_INTERVAL)),
            asyncio.create_task(self._collector_loop("geckoterminal", self.gecko_collector, settings.GECKOTERMINAL_POLL_INTERVAL)),
            asyncio.create_task(self._collector_loop("reddit", self.reddit_collector, settings.REDDIT_POLL_INTERVAL)),
            asyncio.create_task(self._collector_loop("twitter", self.twitter_collector, settings.TWITTER_POLL_INTERVAL)),
            asyncio.create_task(self._collector_loop("trends", self.trends_collector, settings.GOOGLE_TRENDS_POLL_INTERVAL)),
            asyncio.create_task(self._collector_loop("market", self.market_collector, settings.MARKET_CONTEXT_POLL_INTERVAL)),
            asyncio.create_task(self._scoring_loop()),
            asyncio.create_task(self._exit_detection_loop()),
            asyncio.create_task(self._backtest_loop()),
            asyncio.create_task(self._health_check_loop()),
            asyncio.create_task(self._heartbeat_loop()),
            asyncio.create_task(self._cleanup_loop()),
        ]

        # Optional Telegram collector
        if self.telegram_collector and settings.telegram_monitoring_enabled:
            tasks.append(
                asyncio.create_task(self._collector_loop("telegram", self.telegram_collector, 10))
            )

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Tasks cancelled — shutting down")
        finally:
            await self.shutdown()

    def _init_components(self) -> None:
        """Create all collector and analyzer instances."""
        assert self.session is not None

        self.dex_collector = DexScreenerCollector(self.session, self.dex_limiter)
        self.gecko_collector = GeckoTerminalCollector(self.session, self.gecko_limiter)
        self.reddit_collector = RedditCollector(self.session, self.reddit_limiter)
        self.twitter_collector = TwitterCollector(self.session)
        self.trends_collector = GoogleTrendsCollector()
        self.market_collector = MarketContextCollector(self.session, self.cg_limiter)

        if settings.telegram_monitoring_enabled:
            self.telegram_collector = TelegramMonitorCollector()

        self.safety_analyzer = SafetyAnalyzer(self.session, self.goplus_limiter, self.cache)
        self.holder_analyzer = HolderAnalyzer(self.db)
        self.smart_money = SmartMoneyAnalyzer(self.db, self.cache)
        self.deployer_analyzer = DeployerAnalyzer(self.session, self.db, self.cache)
        self.narrative_analyzer = NarrativeAnalyzer(self.db)
        self.insider_detector = InsiderDetector(self.session, self.db, self.cache)
        self.sniper_detector = SniperDetector(self.cache)
        self.exit_detector = ExitDetector(self.db)
        self.scorer = CompositeScorer(self.db)
        self.backtester = Backtester(self.db, self.dex_collector)

    async def _validate_startup(self) -> bool:
        """Validate required services are reachable."""
        ok = True

        # Check Telegram
        if not self.dry_run:
            if not settings.TELEGRAM_BOT_TOKEN:
                logger.error("TELEGRAM_BOT_TOKEN not set")
                ok = False
            if not settings.TELEGRAM_CHAT_ID:
                logger.error("TELEGRAM_CHAT_ID not set")
                ok = False

        # Check DEXScreener
        try:
            async with self.session.get(  # type: ignore[union-attr]
                "https://api.dexscreener.com/token-profiles/latest/v1",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.warning("DEXScreener API returned %d", resp.status)
                else:
                    logger.info("✓ DEXScreener API reachable")
        except Exception as exc:
            logger.error("✗ DEXScreener API unreachable: %s", exc)
            if not self.dry_run:
                ok = False
            else:
                logger.info("  (continuing in dry-run mode)")


        # Check GoPlus
        try:
            async with self.session.get(  # type: ignore[union-attr]
                "https://api.gopluslabs.io/api/v1/token_security/1?contract_addresses=0x0",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    logger.info("✓ GoPlus API reachable")
                else:
                    logger.warning("GoPlus API returned %d (may still work)", resp.status)
        except Exception as exc:
            logger.warning("✗ GoPlus API unreachable: %s — safety checks degraded", exc)

        # Check SQLite
        try:
            await self.db.db.execute("SELECT 1")
            logger.info("✓ SQLite database writable")
        except Exception as exc:
            logger.error("✗ SQLite error: %s", exc)
            ok = False

        return ok

    async def _collector_loop(self, name: str, collector: Any, interval: int) -> None:
        """Run a collector on a fixed interval."""
        if collector is None:
            return

        # Initial warmup delay
        await asyncio.sleep(2)

        while self._running:
            try:
                results = await collector.run_once()
                for item in results:
                    await self.token_queue.put(item)
            except Exception as exc:
                logger.error("Collector %s error: %s", name, exc)
            await asyncio.sleep(interval)

    async def _scoring_loop(self) -> None:
        """Score tokens from the queue and active tokens in the database."""
        # Warmup delay
        logger.info("Scoring loop waiting 30s for warmup...")
        await asyncio.sleep(30)
        logger.info("Scoring loop started")

        while self._running:
            try:
                # Process queue items
                processed = 0
                while not self.token_queue.empty() and processed < 50:
                    try:
                        item = self.token_queue.get_nowait()
                        await self._process_token(item)
                        processed += 1
                    except asyncio.QueueEmpty:
                        break

                # Re-score active tokens
                active = await self.db.get_active_tokens(settings.MAX_AGE_HOURS)
                for token_data in active[:100]:
                    await self._rescore_token(token_data)

            except Exception as exc:
                logger.error("Scoring loop error: %s", exc)

            await asyncio.sleep(settings.SCORING_INTERVAL)

    async def _process_token(self, item: dict) -> None:
        """Process a newly discovered token from a collector."""
        if item.get("type") == "social":
            await self._process_social_signal(item)
            return
        if item.get("type") == "market_context":
            ctx_data = item.get("context", {})
            self.market_context = MarketContext(**ctx_data)
            if self.bot:
                self.bot.update_market_context(self.market_context)
            return

        token_data = item.get("token", {})
        metrics_data = item.get("metrics", {})
        if not token_data.get("contract_address"):
            return

        addr = token_data["contract_address"]
        chain = token_data["chain"]

        # Upsert token
        token_id = await self.db.upsert_token(
            contract_address=addr,
            chain=chain,
            token_name=token_data.get("token_name", ""),
            token_symbol=token_data.get("token_symbol", ""),
            pair_address=token_data.get("pair_address", ""),
            dex=token_data.get("dex", ""),
            deployer_address=token_data.get("deployer_address", ""),
        )

        # Save metrics
        metrics = MetricsSnapshot(**metrics_data) if metrics_data else None
        if metrics:
            await self.db.save_metrics(
                token_id=token_id,
                price_usd=metrics.price_usd,
                volume_h1=metrics.volume_h1,
                volume_h6=metrics.volume_h6,
                volume_h24=metrics.volume_h24,
                liquidity_usd=metrics.liquidity_usd,
                market_cap=metrics.market_cap,
                holder_count=metrics.holder_count,
                buys_h1=metrics.buys_h1,
                sells_h1=metrics.sells_h1,
                price_change_h1=metrics.price_change_h1,
                price_change_h24=metrics.price_change_h24,
            )

        # Safety check (async, may be cached)
        safety = await self.safety_analyzer.check_token(addr, chain) if self.safety_analyzer else None

        if safety:
            await self.db.save_safety_check(
                token_id=token_id,
                is_honeypot=safety.is_honeypot,
                buy_tax=safety.buy_tax,
                sell_tax=safety.sell_tax,
                is_mintable=safety.is_mintable,
                is_open_source=safety.is_open_source,
                hidden_owner=safety.hidden_owner,
                lp_locked=safety.lp_locked,
                lp_lock_duration_days=safety.lp_lock_duration_days,
                safety_score=safety.safety_score,
            )

            if safety.safety_score == 0:
                return  # Rejected by safety gate

        if not metrics:
            return

        # Run analyzers
        volume_result = self.volume_analyzer.analyze(metrics, item)

        holder_result = await self.holder_analyzer.analyze(
            token_id, metrics.holder_count,
        ) if self.holder_analyzer else {}

        smart_money_result = await self.smart_money.analyze(token_id, chain) if self.smart_money else {}

        deployer_result = await self.deployer_analyzer.analyze(
            token_data.get("deployer_address", ""), token_id, chain,
        ) if self.deployer_analyzer and token_data.get("deployer_address") else {}

        narrative_result = await self.narrative_analyzer.analyze(
            token_data.get("token_name", ""), token_data.get("token_symbol", ""),
        ) if self.narrative_analyzer else {}

        insider_result = await self.insider_detector.analyze(
            token_id, addr, chain, token_data.get("deployer_address", ""),
        ) if self.insider_detector else {}

        sniper_result = self.sniper_detector.analyze(
            addr, chain, liquidity_usd=metrics.liquidity_usd,
        ) if self.sniper_detector else {}

        # Build credibility data
        credibility_data = {
            "deployer_score": deployer_result.get("deployer_score", 50),
            "deployer_rejected": deployer_result.get("is_rejected", False),
            "insider_score": insider_result.get("insider_score", 100),
            "insider_penalty": insider_result.get("penalty", 0),
            "sniper_score": sniper_result.get("sniper_score", 100),
            "sniper_penalty": sniper_result.get("penalty", 0),
            "lp_locked": safety.lp_locked if safety else False,
            "dual_source": item.get("source") == "geckoterminal",
        }

        # Score
        score = await self.scorer.score(
            token_id=token_id,
            contract_address=addr,
            chain=chain,
            token_name=token_data.get("token_name", ""),
            token_symbol=token_data.get("token_symbol", ""),
            metrics=metrics,
            safety_result=safety.model_dump() if safety else None,
            volume_analysis=volume_result,
            holder_analysis=holder_result,
            social_data=None,
            smart_money_data=smart_money_result,
            narrative_data=narrative_result,
            credibility_data=credibility_data,
            market_context=self.market_context,
        ) if self.scorer else None

        if score and not score.rejected and self.bot:
            await self.bot.send_alert(
                score=score,
                metrics={
                    **metrics_data,
                    "token_name": token_data.get("token_name", ""),
                    "dex": token_data.get("dex", ""),
                    "volume_acceleration": item.get("volume_acceleration", 0),
                    "holder_growth": holder_result.get("growth_rate_per_hour", 0),
                    "first_seen": None,
                },
                safety=safety.model_dump() if safety else None,
                smart_money=smart_money_result,
                narrative=narrative_result,
                pair_address=token_data.get("pair_address", ""),
            )

    async def _process_social_signal(self, item: dict) -> None:
        """Process a social signal from Reddit, Twitter, or Telegram."""
        addr = item.get("contract_address", "")
        symbol = item.get("token_symbol", "")

        # Try to match to a known token
        if addr:
            for chain in settings.ACTIVE_CHAINS:
                token_id = await self.db.get_token_id(addr, chain)
                if token_id:
                    await self.db.save_social_mention(
                        token_id=token_id,
                        source=item.get("source", "reddit"),
                        mention_count=item.get("mention_count", 1),
                        sentiment_score=item.get("sentiment_score", 0),
                        metadata=item.get("metadata"),
                    )
                    break

    async def _rescore_token(self, token_data: dict) -> None:
        """Re-score an existing token from the database."""
        token_id = token_data["id"]
        addr = token_data["contract_address"]
        chain = token_data["chain"]

        metrics_row = await self.db.get_latest_metrics(token_id)
        if not metrics_row:
            return

        safety_row = await self.db.get_latest_safety(token_id)

        # Only re-score if we have safety data
        if not safety_row:
            return

        metrics = MetricsSnapshot(
            contract_address=addr,
            chain=chain,
            price_usd=metrics_row.get("price_usd", 0),
            volume_h1=metrics_row.get("volume_h1", 0),
            volume_h6=metrics_row.get("volume_h6", 0),
            volume_h24=metrics_row.get("volume_h24", 0),
            liquidity_usd=metrics_row.get("liquidity_usd", 0),
            market_cap=metrics_row.get("market_cap", 0),
            holder_count=metrics_row.get("holder_count", 0),
            buys_h1=metrics_row.get("buys_h1", 0),
            sells_h1=metrics_row.get("sells_h1", 0),
            price_change_h1=metrics_row.get("price_change_h1", 0),
            price_change_h24=metrics_row.get("price_change_h24", 0),
        )

        volume_result = self.volume_analyzer.analyze(metrics)
        holder_result = await self.holder_analyzer.analyze(
            token_id, metrics.holder_count,
        ) if self.holder_analyzer else {}

        if self.scorer:
            await self.scorer.score(
                token_id=token_id,
                contract_address=addr,
                chain=chain,
                token_name=token_data.get("token_name", ""),
                token_symbol=token_data.get("token_symbol", ""),
                metrics=metrics,
                safety_result=safety_row,
                volume_analysis=volume_result,
                holder_analysis=holder_result,
                social_data=None,
                smart_money_data=None,
                narrative_data=None,
                credibility_data=None,
                market_context=self.market_context,
            )

    async def _exit_detection_loop(self) -> None:
        """Check alerted tokens for danger signals."""
        await asyncio.sleep(60)

        while self._running:
            try:
                if self.exit_detector and self.bot:
                    # Build current data map for alerted tokens
                    current_data: dict[str, dict] = {}
                    alerted = await self.db.get_alerted_tokens()
                    for t in alerted:
                        token_id = t["id"]
                        m = await self.db.get_latest_metrics(token_id)
                        if m:
                            key = f"{t['chain']}:{t['contract_address']}"
                            current_data[key] = m

                    dangers = await self.exit_detector.check_all_alerted_tokens(current_data)
                    for danger in dangers:
                        await self.bot.send_danger_alert(danger)

            except Exception as exc:
                logger.error("Exit detection error: %s", exc)

            await asyncio.sleep(settings.EXIT_CHECK_INTERVAL)

    async def _backtest_loop(self) -> None:
        """Run backtesting periodically."""
        await asyncio.sleep(300)  # Wait 5 min before first run

        while self._running:
            try:
                if self.backtester:
                    stats = await self.backtester.run()
                    logger.info("Backtest results: %s", stats)
            except Exception as exc:
                logger.error("Backtest error: %s", exc)

            await asyncio.sleep(settings.BACKTEST_INTERVAL)

    async def _health_check_loop(self) -> None:
        """Monitor collector health and send warnings."""
        await asyncio.sleep(60)

        while self._running:
            try:
                collectors = [
                    self.dex_collector,
                    self.gecko_collector,
                    self.reddit_collector,
                    self.twitter_collector,
                    self.market_collector,
                ]
                for c in collectors:
                    if c and not c.is_healthy and self.bot:
                        await self.bot.send_message(
                            f"⚠️ {c.name} collector has {c.consecutive_failures} "
                            f"consecutive failures\\!",
                        )
            except Exception as exc:
                logger.error("Health check error: %s", exc)

            await asyncio.sleep(settings.HEALTH_CHECK_INTERVAL)

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeat messages."""
        await asyncio.sleep(settings.HEARTBEAT_INTERVAL)

        while self._running:
            try:
                if self.bot:
                    tokens = await self.db.get_active_tokens(max_age_hours=24)
                    stats = await self.db.get_alert_stats(hours=1)
                    uptime = (time.monotonic() - self.start_time) / 3600
                    await self.bot.send_heartbeat(
                        len(tokens), sum(stats.values()), uptime,
                    )
            except Exception as exc:
                logger.error("Heartbeat error: %s", exc)

            await asyncio.sleep(settings.HEARTBEAT_INTERVAL)

    async def _cleanup_loop(self) -> None:
        """Periodically clean up old data."""
        await asyncio.sleep(settings.CLEANUP_INTERVAL)

        while self._running:
            try:
                deleted = await self.db.cleanup_old_data(hours=168)
                cleaned = self.cache.cleanup()
                logger.info("Cleanup: %d DB rows, %d cache entries removed", deleted, cleaned)
            except Exception as exc:
                logger.error("Cleanup error: %s", exc)

            await asyncio.sleep(settings.CLEANUP_INTERVAL)

    async def shutdown(self) -> None:
        """Gracefully shut down all components."""
        logger.info("Shutting down AlphaScanner...")
        self._running = False

        if self.bot:
            try:
                await self.bot.send_message("🔴 AlphaScanner shutting down\\.", )
            except Exception:
                pass
            await self.bot.shutdown()

        if self.telegram_collector:
            await self.telegram_collector.stop_listening()

        if self.session:
            await self.session.close()

        await self.db.close()
        logger.info("AlphaScanner stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="AlphaScanner — Meme Coin Research Agent")
    parser.add_argument("--dry-run", action="store_true", help="Print alerts to console instead of Telegram")
    args = parser.parse_args()

    setup_logging(dry_run=args.dry_run)

    scanner = AlphaScanner(dry_run=args.dry_run)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Graceful shutdown on signals
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.ensure_future(scanner.shutdown()))

    try:
        loop.run_until_complete(scanner.start())
    except KeyboardInterrupt:
        loop.run_until_complete(scanner.shutdown())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
