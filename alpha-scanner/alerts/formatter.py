"""Format token data into clean Telegram messages."""

from __future__ import annotations

from config.chains import get_chain_config
from models.alert import AlertTier, DangerAlert, DangerType
from models.token import TokenScore
from utils.helpers import escape_markdown_v2, format_number, format_percent, format_price, time_ago


def _link(text: str, url: str) -> str:
    return f"[{escape_markdown_v2(text)}]({url})"


def _dexscreener_link(chain: str, pair_address: str) -> str:
    return f"https://dexscreener.com/{chain}/{pair_address}"


def _birdeye_link(address: str) -> str:
    return f"https://birdeye.so/token/{address}"


def _goplus_link(chain: str, address: str) -> str:
    cfg = get_chain_config(chain)
    chain_id = cfg.goplus_chain_id if cfg else "1"
    return f"https://gopluslabs.io/token-security/{chain_id}/{address}"


class AlertFormatter:
    """Format alerts for Telegram MarkdownV2."""

    @staticmethod
    def format_critical_alert(
        score: TokenScore,
        metrics: dict,
        safety: dict | None = None,
        smart_money: dict | None = None,
        narrative: dict | None = None,
        pair_address: str = "",
    ) -> str:
        """Format a CRITICAL tier alert (score 80+)."""
        chain = score.chain
        addr = score.contract_address
        symbol = escape_markdown_v2(score.token_symbol or "???")
        name = escape_markdown_v2(metrics.get("token_name", score.token_symbol or "Unknown"))

        # Badges
        badges: list[str] = []
        if smart_money and smart_money.get("smart_money_buys", 0) > 0:
            badges.append("💰 Smart Money")
        if safety and safety.get("lp_locked"):
            badges.append("🔒 LP Locked")
        if narrative and narrative.get("matched_narratives"):
            badges.append(f"📖 {narrative['strongest_match']}")

        badge_line = " \\| ".join(badges) if badges else ""

        price = format_price(metrics.get("price_usd", 0))
        vol_h1 = format_number(metrics.get("volume_h1", 0))
        liq = format_number(metrics.get("liquidity_usd", 0))
        mcap = format_number(metrics.get("market_cap", 0))
        holders = metrics.get("holder_count", 0)
        holder_growth = metrics.get("holder_growth", 0)
        buys = metrics.get("buys_h1", 0)
        sells = metrics.get("sells_h1", 0)
        total_txns = buys + sells
        buy_pct = int(buys / total_txns * 100) if total_txns > 0 else 50
        price_change = format_percent(metrics.get("price_change_h1", 0))
        vol_accel = metrics.get("volume_acceleration", 0)

        dex_link = _dexscreener_link(chain, pair_address) if pair_address else ""
        bird_link = _birdeye_link(addr)
        gp_link = _goplus_link(chain, addr)

        lines = [
            f"🔴 *CRITICAL ALERT — Score: {score.composite_score:.0f}/100*",
            "",
            f"🪙 *${symbol}* \\({name}\\)",
            f"🔗 Chain: {escape_markdown_v2(chain.title())} \\| DEX: {escape_markdown_v2(metrics.get('dex', 'Unknown'))}",
            f"📍 CA: `{addr}`",
        ]

        if badge_line:
            lines.append(f"🏷️ {badge_line}")

        lines += [
            "",
            "📊 *METRICS:*",
            f"├ Price: {escape_markdown_v2(price)} \\({escape_markdown_v2(price_change)}\\)",
            f"├ Volume 1H: ${escape_markdown_v2(vol_h1)} \\(🔥 {vol_accel:.1f}x avg\\)",
            f"├ Liquidity: ${escape_markdown_v2(liq)}",
            f"├ Market Cap: ${escape_markdown_v2(mcap)}",
            f"├ Holders: {holders:,} \\(▲ {holder_growth}/hr\\)",
            f"└ Buy/Sell Ratio: {buy_pct}% buys",
            "",
            "📈 *SIGNALS:*",
            f"├ Volume: {score.volume_score:.0f}/100",
            f"├ Social: {score.social_score:.0f}/100",
            f"├ Holders: {score.holder_score:.0f}/100",
            f"├ Safety: {score.safety_score:.0f}/100",
            f"├ Smart Money: {score.smart_money_score:.0f}/100",
            f"├ Narrative: {score.narrative_score:.0f}/100",
            f"├ Credibility: {score.credibility_score:.0f}/100",
            f"└ Liquidity: {score.liquidity_score:.0f}/100",
        ]

        if score.bonuses:
            lines.append("")
            lines.append("✅ *BONUSES:*")
            for b in score.bonuses:
                lines.append(f"├ {escape_markdown_v2(b)}")

        if score.penalties:
            lines.append("")
            lines.append("⚠️ *PENALTIES:*")
            for p in score.penalties:
                lines.append(f"├ {escape_markdown_v2(p)}")

        if dex_link:
            lines += [
                "",
                "🔗 *Links:*",
                f"├ [DEXScreener]({dex_link})",
                f"├ [Birdeye]({bird_link})",
                f"└ [GoPlus]({gp_link})",
            ]

        first_seen = metrics.get("first_seen")
        if first_seen:
            lines.append(f"\n⏰ First seen: {escape_markdown_v2(time_ago(first_seen))}")

        if score.score_velocity > 10:
            lines.append(f"📈 Score rising: \\+{score.score_velocity:.0f} pts/hr")

        lines.append("\n⚠️ *DYOR — This is NOT financial advice*")

        return "\n".join(lines)

    @staticmethod
    def format_watch_alert(tokens: list[dict]) -> str:
        """Format a batch of WATCH tier tokens."""
        lines = ["🟡 *WATCHLIST UPDATE*", ""]

        for t in tokens[:10]:
            symbol = escape_markdown_v2(t.get("symbol", "???"))
            chain = escape_markdown_v2(t.get("chain", "").title())
            score = t.get("composite_score", 0)
            price_chg = format_percent(t.get("price_change_h1", 0))
            lines.append(
                f"• *${symbol}* \\({chain}\\) — Score: {score:.0f} \\| {escape_markdown_v2(price_chg)}"
            )

        lines.append("\n_Use /token <address> for details_")
        return "\n".join(lines)

    @staticmethod
    def format_danger_alert(danger: DangerAlert) -> str:
        """Format a danger/exit alert."""
        emoji_map = {
            DangerType.SMART_MONEY_EXIT: "💀",
            DangerType.LP_REMOVAL: "🚨",
            DangerType.HOLDER_DUMP: "📉",
            DangerType.VOLUME_CRASH: "🔻",
            DangerType.PRICE_CRASH: "💥",
            DangerType.DEPLOYER_MOVE: "⚠️",
        }
        emoji = emoji_map.get(danger.danger_type, "⚠️")
        severity_emoji = "🔴" if danger.severity == "CRITICAL" else "🟠"

        symbol = escape_markdown_v2(danger.token_symbol or "???")
        chain = escape_markdown_v2(danger.chain.title())
        details = escape_markdown_v2(danger.details)
        dtype = escape_markdown_v2(danger.danger_type.value.replace("_", " ").title())

        lines = [
            f"{emoji} {severity_emoji} *DANGER ALERT — {dtype}*",
            "",
            f"🪙 *${symbol}* \\({chain}\\)",
            f"📍 CA: `{danger.contract_address}`",
            "",
            f"⚠️ {details}",
            "",
            "_Consider exiting this position\\._",
        ]

        return "\n".join(lines)

    @staticmethod
    def format_heartbeat(
        tokens_tracked: int,
        alerts_last_hour: int,
        uptime_hours: float,
        market_context: dict | None = None,
    ) -> str:
        """Format a heartbeat status message."""
        lines = [
            f"💚 AlphaScanner running — {tokens_tracked} tokens tracked, "
            f"{alerts_last_hour} alerts in last hour",
        ]
        if market_context:
            btc = market_context.get("btc_trend", "neutral")
            fg = market_context.get("fear_greed_index", 50)
            lines.append(f"📊 Market: BTC {btc}, F&G: {fg}")
        lines.append(f"⏱️ Uptime: {uptime_hours:.1f}h")
        return "\n".join(lines)
