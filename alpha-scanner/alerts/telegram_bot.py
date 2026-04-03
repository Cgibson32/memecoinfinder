"""Telegram bot for sending alerts and handling user commands."""

from __future__ import annotations

import logging
from typing import Any

from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from alerts.formatter import AlertFormatter
from config.settings import settings
from models.alert import Alert, AlertTier, DangerAlert
from models.token import MarketContext, TokenScore
from storage.cache import TTLCache
from storage.database import Database
from utils.helpers import format_number, format_percent

logger = logging.getLogger(__name__)


class TelegramAlertBot:
    """Telegram bot for alerts and commands."""

    def __init__(self, db: Database, cache: TTLCache, dry_run: bool = False) -> None:
        self.db = db
        self.cache = cache
        self.dry_run = dry_run
        self.formatter = AlertFormatter()
        self._paused = False
        self._app: Application | None = None
        self._bot: Bot | None = None
        self._market_context: MarketContext | None = None
        self._start_time: float = 0.0

    async def initialize(self) -> None:
        """Set up the bot and register command handlers."""
        if self.dry_run:
            logger.info("Telegram bot in dry-run mode — alerts print to console")
            return

        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            logger.error("TELEGRAM_BOT_TOKEN not set")
            return

        self._app = Application.builder().token(token).build()
        self._bot = self._app.bot

        # Register command handlers
        handlers = [
            ("start", self._cmd_start),
            ("status", self._cmd_status),
            ("top", self._cmd_top),
            ("watchlist", self._cmd_watchlist),
            ("token", self._cmd_token),
            ("settings", self._cmd_settings),
            ("pause", self._cmd_pause),
            ("resume", self._cmd_resume),
            ("stats", self._cmd_stats),
            ("market", self._cmd_market),
            ("dangers", self._cmd_dangers),
        ]
        for name, handler in handlers:
            self._app.add_handler(CommandHandler(name, handler))

        await self._app.initialize()
        await self._app.start()
        if self._app.updater:
            await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram bot started")

    async def shutdown(self) -> None:
        if self._app:
            if self._app.updater:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    async def send_message(self, text: str, parse_mode: str = "MarkdownV2") -> None:
        """Send a message to the configured chat."""
        if self.dry_run:
            print(f"\n[TELEGRAM] {text}\n")
            return

        if not self._bot:
            return

        try:
            await self._bot.send_message(
                chat_id=settings.TELEGRAM_CHAT_ID,
                text=text,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            logger.error("Failed to send Telegram message: %s", exc)
            # Retry without markdown if it fails
            try:
                await self._bot.send_message(
                    chat_id=settings.TELEGRAM_CHAT_ID,
                    text=text.replace("\\", ""),
                    disable_web_page_preview=True,
                )
            except Exception:
                pass

    async def send_alert(
        self,
        score: TokenScore,
        metrics: dict,
        safety: dict | None = None,
        smart_money: dict | None = None,
        narrative: dict | None = None,
        pair_address: str = "",
    ) -> bool:
        """Send an alert based on score tier. Returns True if sent."""
        if self._paused:
            return False

        tier = self._get_tier(score.composite_score)
        if not tier:
            return False

        # Deduplication: same token + same tier within 60 minutes
        dedup_key = f"alert:{score.chain}:{score.contract_address}:{tier.value}"
        if self.cache.has(dedup_key):
            return False

        if tier == AlertTier.CRITICAL:
            text = self.formatter.format_critical_alert(
                score, metrics, safety, smart_money, narrative, pair_address,
            )
            await self.send_message(text)
        elif tier == AlertTier.WATCH:
            # Watch alerts are batched — store for batch send
            self.cache.set(
                f"watch_batch:{score.chain}:{score.contract_address}",
                {
                    "symbol": score.token_symbol,
                    "chain": score.chain,
                    "composite_score": score.composite_score,
                    "price_change_h1": metrics.get("price_change_h1", 0),
                },
                ttl_seconds=300,
            )
            return False  # Will be sent in batch

        # Log the alert
        token_id = await self.db.get_token_id(score.contract_address, score.chain)
        if token_id:
            await self.db.log_alert(token_id, tier.value, score.composite_score, "")

        self.cache.set(dedup_key, True, ttl_seconds=3600)
        return True

    async def send_watch_batch(self) -> None:
        """Send batched WATCH tier alerts."""
        if self._paused:
            return

        # Collect all watch items from cache
        watch_items: list[dict] = []
        # Scan cache for watch batch items (simplified — in practice would use a dedicated list)
        # For now, this is called periodically and the batch is managed externally
        pass

    async def send_danger_alert(self, danger: DangerAlert) -> bool:
        """Send a danger/exit alert immediately."""
        dedup_key = f"danger:{danger.chain}:{danger.contract_address}:{danger.danger_type.value}"
        if self.cache.has(dedup_key):
            return False

        text = self.formatter.format_danger_alert(danger)
        await self.send_message(text)

        token_id = await self.db.get_token_id(danger.contract_address, danger.chain)
        if token_id:
            await self.db.log_danger_alert(
                token_id=token_id,
                danger_type=danger.danger_type.value,
                severity=danger.severity,
                details=danger.details,
                current_value=danger.current_value,
                previous_value=danger.previous_value,
                change_pct=danger.change_pct,
            )

        self.cache.set(dedup_key, True, ttl_seconds=3600)
        return True

    async def send_heartbeat(
        self, tokens_tracked: int, alerts_last_hour: int, uptime_hours: float
    ) -> None:
        """Send periodic heartbeat message."""
        ctx = self._market_context.model_dump() if self._market_context else None
        text = self.formatter.format_heartbeat(tokens_tracked, alerts_last_hour, uptime_hours, ctx)
        await self.send_message(text, parse_mode="")

    def update_market_context(self, ctx: MarketContext) -> None:
        self._market_context = ctx

    def _get_tier(self, score: float) -> AlertTier | None:
        if score >= settings.ALERT_CRITICAL_THRESHOLD:
            return AlertTier.CRITICAL
        if score >= settings.ALERT_WATCH_THRESHOLD:
            return AlertTier.WATCH
        if score >= settings.ALERT_RADAR_THRESHOLD:
            return AlertTier.RADAR
        return None

    # --- Command Handlers ---

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(  # type: ignore[union-attr]
            "🚀 *AlphaScanner Bot*\n\n"
            "I find high-potential meme coins before they pump.\n\n"
            "Commands:\n"
            "/top - Top 10 tokens right now\n"
            "/watchlist - Tokens on watch\n"
            "/token <address> - Lookup a token\n"
            "/status - Agent health\n"
            "/stats - 24h statistics\n"
            "/market - Market context\n"
            "/dangers - Active danger signals\n"
            "/settings - Current config\n"
            "/pause - Pause alerts\n"
            "/resume - Resume alerts",
            parse_mode="Markdown",
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        tokens = await self.db.get_active_tokens(max_age_hours=24)
        stats = await self.db.get_alert_stats(hours=24)
        total_alerts = sum(stats.values())
        text = (
            f"📊 *Status*\n"
            f"Active tokens: {len(tokens)}\n"
            f"Alerts (24h): {total_alerts}\n"
            f"  Critical: {stats.get('CRITICAL', 0)}\n"
            f"  Watch: {stats.get('WATCH', 0)}\n"
            f"  Radar: {stats.get('RADAR', 0)}\n"
            f"Paused: {'Yes' if self._paused else 'No'}"
        )
        await update.message.reply_text(text, parse_mode="Markdown")  # type: ignore[union-attr]

    async def _cmd_top(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        top = await self.db.get_top_tokens(limit=10)
        if not top:
            await update.message.reply_text("No scored tokens yet.")  # type: ignore[union-attr]
            return

        lines = ["🏆 *Top 10 Tokens*\n"]
        for i, t in enumerate(top, 1):
            symbol = t.get("token_symbol", "???")
            chain = t.get("chain", "").title()
            score = t.get("composite_score", 0)
            lines.append(f"{i}. *${symbol}* ({chain}) — Score: {score:.0f}")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")  # type: ignore[union-attr]

    async def _cmd_watchlist(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        alerts = await self.db.get_recent_alerts(hours=24, tier="WATCH")
        if not alerts:
            await update.message.reply_text("No tokens on watchlist.")  # type: ignore[union-attr]
            return

        lines = ["🟡 *Watchlist*\n"]
        for a in alerts[:15]:
            symbol = a.get("token_symbol", "???")
            score = a.get("composite_score", 0)
            lines.append(f"• ${symbol} — Score: {score:.0f}")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")  # type: ignore[union-attr]

    async def _cmd_token(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /token <contract_address>")  # type: ignore[union-attr]
            return

        address = args[0]
        # Search all chains
        for chain in settings.ACTIVE_CHAINS:
            token = await self.db.get_token(address, chain)
            if token:
                token_id = token["id"]
                score = await self.db.get_latest_score(token_id)
                metrics = await self.db.get_latest_metrics(token_id)
                safety = await self.db.get_latest_safety(token_id)

                lines = [
                    f"🪙 *${token.get('token_symbol', '???')}* ({chain.title()})",
                    f"CA: `{address}`",
                    "",
                ]

                if score:
                    lines += [
                        f"📊 *Score: {score['composite_score']:.0f}/100*",
                        f"├ Volume: {score['volume_score']:.0f}",
                        f"├ Social: {score['social_score']:.0f}",
                        f"├ Holders: {score['holder_score']:.0f}",
                        f"├ Safety: {score['safety_score']:.0f}",
                        f"├ Smart Money: {score.get('smart_money_score', 0):.0f}",
                        f"├ Narrative: {score.get('narrative_score', 0):.0f}",
                        f"├ Credibility: {score.get('credibility_score', 0):.0f}",
                        f"└ Liquidity: {score['liquidity_score']:.0f}",
                    ]

                if metrics:
                    lines += [
                        "",
                        f"💰 Price: ${metrics.get('price_usd', 0):.8f}",
                        f"📊 Volume 1H: ${format_number(metrics.get('volume_h1', 0))}",
                        f"💧 Liquidity: ${format_number(metrics.get('liquidity_usd', 0))}",
                        f"👥 Holders: {metrics.get('holder_count', 0)}",
                    ]

                if safety:
                    hp = "❌ Yes" if safety.get("is_honeypot") else "✅ No"
                    lp = "🔒 Locked" if safety.get("lp_locked") else "🔓 Unlocked"
                    lines += ["", f"🛡️ Honeypot: {hp}", f"💧 LP: {lp}"]

                await update.message.reply_text("\n".join(lines), parse_mode="Markdown")  # type: ignore[union-attr]
                return

        await update.message.reply_text(f"Token {address} not found in database.")  # type: ignore[union-attr]

    async def _cmd_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        w = settings.weights_dict
        text = (
            "⚙️ *Settings*\n\n"
            "*Scoring Weights:*\n"
            + "\n".join(f"  {k}: {v:.2f}" for k, v in w.items())
            + f"\n\n*Filters:*\n"
            f"  Min Liquidity: ${settings.MIN_LIQUIDITY_USD:,.0f}\n"
            f"  Max Age: {settings.MAX_AGE_HOURS}h\n"
            f"  Min Txns/1H: {settings.MIN_TRANSACTIONS_1H}\n"
            f"  Max Tax: {settings.MAX_SELL_TAX_PERCENT}%\n"
            f"\n*Chains:* {', '.join(settings.ACTIVE_CHAINS)}"
        )
        await update.message.reply_text(text, parse_mode="Markdown")  # type: ignore[union-attr]

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._paused = True
        await update.message.reply_text("⏸️ Alerts paused. Scanner still running.")  # type: ignore[union-attr]

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._paused = False
        await update.message.reply_text("▶️ Alerts resumed.")  # type: ignore[union-attr]

    async def _cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        stats = await self.db.get_alert_stats(hours=24)
        backtest = await self.db.get_backtest_stats()
        tokens = await self.db.get_active_tokens(max_age_hours=24)

        lines = [
            "📈 *24h Statistics*",
            f"Tokens scanned: {len(tokens)}",
            f"Critical alerts: {stats.get('CRITICAL', 0)}",
            f"Watch alerts: {stats.get('WATCH', 0)}",
            f"Radar entries: {stats.get('RADAR', 0)}",
            "",
            "🎯 *Backtesting*",
            f"Total assessed: {backtest.get('total', 0)}",
            f"Hit rate (4h, >50%): {backtest.get('hit_rate_4h', 0):.1f}%",
            f"Hit rate (24h, >100%): {backtest.get('hit_rate_24h', 0):.1f}%",
            f"Avg return (4h): {backtest.get('avg_return_4h', 0):.1f}%",
            f"Best pick: {backtest.get('best_return', 0):.0f}%",
            f"Worst pick: {backtest.get('worst_return', 0):.0f}%",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")  # type: ignore[union-attr]

    async def _cmd_market(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        ctx = self._market_context
        if not ctx:
            await update.message.reply_text("Market context not available yet.")  # type: ignore[union-attr]
            return

        text = (
            f"📊 *Market Context*\n\n"
            f"₿ BTC: ${ctx.btc_price:,.0f} ({format_percent(ctx.btc_change_24h)})\n"
            f"📈 Trend: {ctx.btc_trend.title()}\n"
            f"😱 Fear & Greed: {ctx.fear_greed_index} ({ctx.fear_greed_label})\n"
            f"🎭 Meme Sector: {ctx.meme_sector_trend.title()}"
        )
        await update.message.reply_text(text, parse_mode="Markdown")  # type: ignore[union-attr]

    async def _cmd_dangers(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        async with self.db.db.execute(
            """SELECT da.*, t.token_symbol, t.chain
               FROM danger_alerts da
               JOIN tokens t ON da.token_id = t.id
               WHERE da.sent_at > datetime('now', '-24 hours')
               ORDER BY da.sent_at DESC LIMIT 10""",
        ) as cursor:
            rows = [dict(r) for r in await cursor.fetchall()]

        if not rows:
            await update.message.reply_text("No active danger signals.")  # type: ignore[union-attr]
            return

        lines = ["⚠️ *Active Danger Signals*\n"]
        for r in rows:
            emoji = "🔴" if r.get("severity") == "CRITICAL" else "🟠"
            symbol = r.get("token_symbol", "???")
            dtype = r.get("danger_type", "").replace("_", " ").title()
            lines.append(f"{emoji} ${symbol} — {dtype}")
            lines.append(f"   {r.get('details', '')}")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")  # type: ignore[union-attr]
