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
        # Set by main.py for on-demand lookups
        self.scanner: Any = None

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
            ("search", self._cmd_search),
            ("scan", self._cmd_search),
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
            "/search <query> - Search & score ANY coin (name, symbol, or address)\n"
            "/top - Top 10 tokens right now\n"
            "/watchlist - Tokens on watch\n"
            "/token <address> - Lookup a tracked token\n"
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

    async def _cmd_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Search for any token by name, symbol, or address and score it on-demand."""
        args = context.args
        if not args:
            await update.message.reply_text(  # type: ignore[union-attr]
                "Usage: /search <query>\n\n"
                "Examples:\n"
                "  /search PEPE\n"
                "  /search dogwifhat\n"
                "  /search 0x6982508...\n"
                "  /search HbTd4Cv9..."
            )
            return

        query = " ".join(args)
        await update.message.reply_text(f"Searching for '{query}'...")  # type: ignore[union-attr]

        if not self.scanner or not self.scanner.session:
            await update.message.reply_text("Scanner not ready yet.")  # type: ignore[union-attr]
            return

        try:
            import aiohttp
            # Search DEXScreener
            url = f"https://api.dexscreener.com/latest/dex/search?q={query}"
            async with self.scanner.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    await update.message.reply_text(f"DEXScreener search failed (HTTP {resp.status})")  # type: ignore[union-attr]
                    return
                data = await resp.json()

            pairs = data.get("pairs", [])
            if not pairs:
                await update.message.reply_text(f"No results found for '{query}'")  # type: ignore[union-attr]
                return

            # Show top 5 results
            lines = [f"Search results for '{query}':\n"]
            shown = 0
            seen_tokens: set[str] = set()

            for pair in pairs[:20]:
                base = pair.get("baseToken", {})
                addr = base.get("address", "")
                symbol = base.get("symbol", "")
                chain = pair.get("chainId", "").lower()

                key = f"{chain}:{addr}"
                if key in seen_tokens:
                    continue
                seen_tokens.add(key)

                if chain not in settings.ACTIVE_CHAINS:
                    continue

                name = base.get("name", "")
                price = pair.get("priceUsd", "0")
                liq = pair.get("liquidity", {}).get("usd", 0)
                vol24 = pair.get("volume", {}).get("h24", 0)
                mcap = pair.get("marketCap", 0) or pair.get("fdv", 0)
                chg24 = pair.get("priceChange", {}).get("h24", 0)
                pair_addr = pair.get("pairAddress", "")
                dex = pair.get("dexId", "")

                txns = pair.get("txns", {})
                buys_h1 = int(txns.get("h1", {}).get("buys", 0) or 0)
                sells_h1 = int(txns.get("h1", {}).get("sells", 0) or 0)
                vol_h1 = float(pair.get("volume", {}).get("h1", 0) or 0)
                vol_h6 = float(pair.get("volume", {}).get("h6", 0) or 0)

                # Quick score this token
                from models.token import MetricsSnapshot
                metrics = MetricsSnapshot(
                    contract_address=addr, chain=chain,
                    price_usd=float(price or 0),
                    volume_h1=vol_h1, volume_h6=vol_h6,
                    volume_h24=float(vol24 or 0),
                    liquidity_usd=float(liq or 0),
                    market_cap=float(mcap or 0),
                    buys_h1=buys_h1, sells_h1=sells_h1,
                    price_change_h1=float(pair.get("priceChange", {}).get("h1", 0) or 0),
                    price_change_h24=float(chg24 or 0),
                )

                vol_result = self.scanner.volume_analyzer.analyze(metrics)
                vol_score = vol_result.get("volume_score", 0)

                # Safety check
                safety = None
                safety_score = 50.0
                if self.scanner.safety_analyzer:
                    safety = await self.scanner.safety_analyzer.check_token(addr, chain)
                    if safety:
                        safety_score = safety.safety_score

                # Save to DB
                token_id = await self.db.upsert_token(
                    contract_address=addr, chain=chain,
                    token_name=name, token_symbol=symbol,
                    pair_address=pair_addr, dex=dex,
                )
                await self.db.save_metrics(
                    token_id=token_id, price_usd=metrics.price_usd,
                    volume_h1=metrics.volume_h1, volume_h6=metrics.volume_h6,
                    volume_h24=metrics.volume_h24, liquidity_usd=metrics.liquidity_usd,
                    market_cap=metrics.market_cap, buys_h1=metrics.buys_h1,
                    sells_h1=metrics.sells_h1, price_change_h1=metrics.price_change_h1,
                    price_change_h24=metrics.price_change_h24,
                )

                # Quick composite
                liq_score = min(float(liq or 0) / 50000 * 60, 100)
                total_txns = buys_h1 + sells_h1
                buy_pct = int(buys_h1 / total_txns * 100) if total_txns > 0 else 50
                quick_score = (vol_score * 0.30 + safety_score * 0.25 + liq_score * 0.25 + 25 * 0.20)

                await self.db.save_score(
                    token_id=token_id, composite=quick_score,
                    volume=vol_score, social=0, holder=0,
                    safety=safety_score, liquidity=liq_score,
                )

                # Safety indicator
                safe_str = ""
                if safety and safety.is_honeypot:
                    safe_str = " HONEYPOT"
                elif safety and safety_score >= 80:
                    safe_str = " Safe"
                elif safety and safety_score < 40:
                    safe_str = " Risky"

                chg_str = f"+{chg24:.0f}%" if chg24 and chg24 >= 0 else f"{chg24:.0f}%" if chg24 else "N/A"

                lines.append(
                    f"{shown+1}. ${symbol} ({name[:20]}){safe_str}\n"
                    f"   {chain.title()} | {dex}\n"
                    f"   Price: ${float(price or 0):.8f} ({chg_str} 24h)\n"
                    f"   Liq: ${format_number(float(liq or 0))} | Vol24h: ${format_number(float(vol24 or 0))}\n"
                    f"   MCap: ${format_number(float(mcap or 0))}\n"
                    f"   Buys/Sells 1h: {buys_h1}/{sells_h1} | Buy%: {buy_pct}%\n"
                    f"   Score: {quick_score:.0f}/100 [vol={vol_score:.0f} safe={safety_score:.0f} liq={liq_score:.0f}]\n"
                )
                shown += 1
                if shown >= 5:
                    break

            if shown == 0:
                lines.append("No tokens found on supported chains.")

            await update.message.reply_text("\n".join(lines))  # type: ignore[union-attr]

        except Exception as exc:
            logger.error("Search command error: %s", exc)
            await update.message.reply_text(f"Search error: {exc}")  # type: ignore[union-attr]
