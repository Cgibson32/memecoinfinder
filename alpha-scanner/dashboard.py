#!/usr/bin/env python3
"""AlphaScanner Streamlit Dashboard — run with: streamlit run dashboard.py"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

DB_PATH = "alpha_scanner.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------- Page Config ----------
st.set_page_config(page_title="AlphaScanner Dashboard", page_icon="🔍", layout="wide")

# ---------- Sidebar Navigation ----------
page = st.sidebar.radio(
    "Navigation",
    ["🏆 Live Leaderboard", "🔎 Token Deep Dive", "📋 Alert History", "⚠️ Danger Signals", "🖥️ System Health"],
)

# ========== PAGE 1: LIVE LEADERBOARD ==========
if page == "🏆 Live Leaderboard":
    st.title("🏆 Live Leaderboard")
    st.caption("Top tokens by composite score — auto-refreshes every 30 seconds")

    auto_refresh = st.checkbox("Auto-refresh", value=True)
    if auto_refresh:
        st.empty()
        import time
        # Streamlit auto-rerun placeholder
        st.markdown(
            '<meta http-equiv="refresh" content="30">',
            unsafe_allow_html=True,
        )

    conn = get_conn()
    rows = conn.execute(
        """SELECT t.*, s.composite_score, s.volume_score, s.social_score,
                  s.holder_score, s.safety_score, s.liquidity_score,
                  s.smart_money_score, s.narrative_score, s.credibility_score,
                  s.score_velocity, s.scored_at,
                  m.price_usd, m.volume_h1, m.liquidity_usd, m.holder_count,
                  m.price_change_h1
           FROM tokens t
           JOIN scores s ON s.token_id = t.id
             AND s.id = (SELECT MAX(id) FROM scores WHERE token_id = t.id)
           LEFT JOIN metrics m ON m.token_id = t.id
             AND m.id = (SELECT MAX(id) FROM metrics WHERE token_id = t.id)
           ORDER BY s.composite_score DESC
           LIMIT 50""",
    ).fetchall()
    conn.close()

    if not rows:
        st.info("No scored tokens yet. Start the scanner with `python main.py`")
    else:
        # Build dataframe
        data = []
        for i, r in enumerate(rows, 1):
            score = r["composite_score"] or 0

            # Color indicator
            if score >= 80:
                indicator = "🔴"
            elif score >= 60:
                indicator = "🟡"
            elif score >= 40:
                indicator = "🟢"
            else:
                indicator = "⚪"

            # Badges
            badges = []
            if (r["smart_money_score"] or 0) > 30:
                badges.append("💰")
            if (r["narrative_score"] or 0) > 30:
                badges.append("📖")

            change = r["price_change_h1"] or 0
            trend = f"▲ {change:.1f}%" if change >= 0 else f"▼ {abs(change):.1f}%"

            data.append({
                "Rank": f"{indicator} {i}",
                "Symbol": f"${r['token_symbol'] or '???'}",
                "Chain": (r["chain"] or "").title(),
                "Score": f"{score:.0f}",
                "Price": f"${r['price_usd']:.8f}" if r["price_usd"] and r["price_usd"] < 1 else f"${r['price_usd']:.4f}" if r["price_usd"] else "N/A",
                "Vol 1H": _fmt_num(r["volume_h1"]),
                "Liquidity": _fmt_num(r["liquidity_usd"]),
                "Holders": r["holder_count"] or 0,
                "Trend": trend,
                "Badges": " ".join(badges),
                "Safety": f"{r['safety_score']:.0f}" if r["safety_score"] else "?",
            })

        st.dataframe(data, use_container_width=True, height=700)


# ========== PAGE 2: TOKEN DEEP DIVE ==========
elif page == "🔎 Token Deep Dive":
    st.title("🔎 Token Deep Dive")

    search = st.text_input("Search by contract address or symbol")

    if search:
        conn = get_conn()
        token = conn.execute(
            """SELECT * FROM tokens
               WHERE contract_address LIKE ? OR token_symbol LIKE ?
               LIMIT 1""",
            (f"%{search}%", f"%{search}%"),
        ).fetchone()

        if not token:
            st.warning(f"Token '{search}' not found")
        else:
            token_id = token["id"]
            st.subheader(f"${token['token_symbol'] or '???'} — {token['token_name'] or 'Unknown'}")
            st.caption(f"Chain: {(token['chain'] or '').title()} | CA: `{token['contract_address']}`")

            col1, col2 = st.columns(2)

            # Latest score
            score = conn.execute(
                "SELECT * FROM scores WHERE token_id = ? ORDER BY scored_at DESC LIMIT 1",
                (token_id,),
            ).fetchone()

            if score:
                with col1:
                    st.metric("Composite Score", f"{score['composite_score']:.0f}/100")

                    scores_data = {
                        "Component": ["Volume", "Social", "Holders", "Safety", "Liquidity", "Smart Money", "Narrative", "Credibility"],
                        "Score": [
                            score["volume_score"] or 0,
                            score["social_score"] or 0,
                            score["holder_score"] or 0,
                            score["safety_score"] or 0,
                            score["liquidity_score"] or 0,
                            score.get("smart_money_score") or 0,
                            score.get("narrative_score") or 0,
                            score.get("credibility_score") or 0,
                        ],
                    }
                    fig = px.bar(
                        scores_data, x="Component", y="Score",
                        title="Score Breakdown",
                        color="Score",
                        color_continuous_scale="RdYlGn",
                        range_y=[0, 100],
                    )
                    st.plotly_chart(fig, use_container_width=True)

            # Metrics
            metrics = conn.execute(
                "SELECT * FROM metrics WHERE token_id = ? ORDER BY recorded_at DESC LIMIT 1",
                (token_id,),
            ).fetchone()

            if metrics:
                with col2:
                    st.metric("Price", f"${metrics['price_usd']:.8f}" if metrics["price_usd"] else "N/A")
                    mc1, mc2 = st.columns(2)
                    mc1.metric("Volume 1H", _fmt_num(metrics["volume_h1"]))
                    mc2.metric("Liquidity", _fmt_num(metrics["liquidity_usd"]))
                    mc1.metric("Holders", metrics["holder_count"] or 0)
                    mc2.metric("Market Cap", _fmt_num(metrics["market_cap"]))

            # Score history chart
            score_history = conn.execute(
                "SELECT composite_score, scored_at FROM scores WHERE token_id = ? ORDER BY scored_at ASC LIMIT 200",
                (token_id,),
            ).fetchall()

            if score_history:
                st.subheader("Score History")
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=[r["scored_at"] for r in score_history],
                    y=[r["composite_score"] for r in score_history],
                    mode="lines",
                    name="Composite Score",
                ))
                fig.update_layout(yaxis_range=[0, 100])
                st.plotly_chart(fig, use_container_width=True)

            # Price/volume history
            metric_history = conn.execute(
                "SELECT price_usd, volume_h1, recorded_at FROM metrics WHERE token_id = ? ORDER BY recorded_at ASC LIMIT 200",
                (token_id,),
            ).fetchall()

            if metric_history:
                st.subheader("Price & Volume History")
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=[r["recorded_at"] for r in metric_history],
                    y=[r["price_usd"] for r in metric_history],
                    mode="lines",
                    name="Price (USD)",
                ))
                st.plotly_chart(fig, use_container_width=True)

            # Safety
            safety = conn.execute(
                "SELECT * FROM safety_checks WHERE token_id = ? ORDER BY checked_at DESC LIMIT 1",
                (token_id,),
            ).fetchone()

            if safety:
                st.subheader("Safety Check")
                sc1, sc2, sc3 = st.columns(3)
                sc1.metric("Honeypot", "❌ Yes" if safety["is_honeypot"] else "✅ No")
                sc2.metric("Buy Tax", f"{safety['buy_tax']:.1f}%")
                sc3.metric("Sell Tax", f"{safety['sell_tax']:.1f}%")
                sc1.metric("Mintable", "⚠️ Yes" if safety["is_mintable"] else "✅ No")
                sc2.metric("Open Source", "✅ Yes" if safety["is_open_source"] else "⚠️ No")
                sc3.metric("LP Locked", "🔒 Yes" if safety.get("lp_locked") else "🔓 No")

            # Smart money activity
            smart_trades = conn.execute(
                """SELECT t.*, w.wallet_address, w.success_rate
                   FROM smart_money_trades t
                   JOIN smart_money_wallets w ON t.wallet_id = w.id
                   WHERE t.token_id = ? ORDER BY t.traded_at DESC LIMIT 10""",
                (token_id,),
            ).fetchall()

            if smart_trades:
                st.subheader("💰 Smart Money Activity")
                for t in smart_trades:
                    emoji = "🟢" if t["action"] == "buy" else "🔴"
                    st.write(
                        f"{emoji} {t['wallet_address'][:8]}...{t['wallet_address'][-4:]} "
                        f"— {t['action'].upper()} ${_fmt_num(t['amount_usd'])} "
                        f"(success rate: {t['success_rate']:.0%})"
                    )

            # Links
            st.subheader("🔗 Links")
            addr = token["contract_address"]
            chain = token["chain"]
            pair = token.get("pair_address", "")
            if pair:
                st.markdown(f"[DEXScreener](https://dexscreener.com/{chain}/{pair})")
            st.markdown(f"[Birdeye](https://birdeye.so/token/{addr})")

        conn.close()


# ========== PAGE 3: ALERT HISTORY ==========
elif page == "📋 Alert History":
    st.title("📋 Alert History")

    col1, col2, col3 = st.columns(3)
    tier_filter = col1.selectbox("Tier", ["All", "CRITICAL", "WATCH", "RADAR"])
    chain_filter = col2.selectbox("Chain", ["All", "solana", "ethereum", "base", "bsc"])
    hours_filter = col3.slider("Last N hours", 1, 168, 24)

    conn = get_conn()

    query = """SELECT a.*, t.token_symbol, t.chain, t.contract_address
               FROM alerts a JOIN tokens t ON a.token_id = t.id
               WHERE a.sent_at > datetime('now', ? || ' hours')"""
    params: list = [f"-{hours_filter}"]

    if tier_filter != "All":
        query += " AND a.tier = ?"
        params.append(tier_filter)
    if chain_filter != "All":
        query += " AND t.chain = ?"
        params.append(chain_filter)

    query += " ORDER BY a.sent_at DESC LIMIT 200"
    alerts = conn.execute(query, params).fetchall()

    if not alerts:
        st.info("No alerts in the selected period")
    else:
        data = [
            {
                "Time": r["sent_at"],
                "Tier": r["tier"],
                "Symbol": f"${r['token_symbol'] or '???'}",
                "Chain": (r["chain"] or "").title(),
                "Score": f"{r['composite_score']:.0f}",
                "Address": r["contract_address"][:12] + "...",
            }
            for r in alerts
        ]
        st.dataframe(data, use_container_width=True, height=500)

    # Backtesting stats
    st.subheader("🎯 Backtesting Results")
    bt_stats = conn.execute(
        """SELECT COUNT(*) as total,
                  SUM(is_hit_4h) as hits_4h,
                  SUM(is_hit_24h) as hits_24h,
                  AVG(return_4h_pct) as avg_ret_4h,
                  AVG(return_24h_pct) as avg_ret_24h,
                  MAX(return_24h_pct) as best,
                  MIN(return_24h_pct) as worst
           FROM backtest_results""",
    ).fetchone()

    if bt_stats and bt_stats["total"] and bt_stats["total"] > 0:
        c1, c2, c3, c4 = st.columns(4)
        total = bt_stats["total"]
        c1.metric("Alerts Assessed", total)
        c2.metric("Hit Rate (4h >50%)", f"{(bt_stats['hits_4h'] or 0) / total * 100:.1f}%")
        c3.metric("Hit Rate (24h >100%)", f"{(bt_stats['hits_24h'] or 0) / total * 100:.1f}%")
        c4.metric("Avg Return (4h)", f"{bt_stats['avg_ret_4h'] or 0:.1f}%")
        c1.metric("Best Pick", f"{bt_stats['best'] or 0:.0f}%")
        c2.metric("Worst Pick", f"{bt_stats['worst'] or 0:.0f}%")
    else:
        st.info("No backtest results yet — needs at least 4 hours of alert history")

    conn.close()


# ========== PAGE 4: DANGER SIGNALS ==========
elif page == "⚠️ Danger Signals":
    st.title("⚠️ Danger Signals")
    st.caption("Active exit signals on previously-alerted tokens")

    conn = get_conn()
    dangers = conn.execute(
        """SELECT da.*, t.token_symbol, t.chain, t.contract_address
           FROM danger_alerts da
           JOIN tokens t ON da.token_id = t.id
           WHERE da.sent_at > datetime('now', '-24 hours')
           ORDER BY da.sent_at DESC LIMIT 50""",
    ).fetchall()

    if not dangers:
        st.success("No active danger signals — all clear!")
    else:
        for d in dangers:
            severity_color = "red" if d["severity"] == "CRITICAL" else "orange"
            dtype = (d["danger_type"] or "").replace("_", " ").title()
            symbol = d["token_symbol"] or "???"

            st.markdown(
                f"**:{severity_color}[{d['severity']}]** — "
                f"**${symbol}** ({(d['chain'] or '').title()}) — {dtype}"
            )
            st.caption(f"{d['details']} | {d['sent_at']}")
            st.divider()

    conn.close()


# ========== PAGE 5: SYSTEM HEALTH ==========
elif page == "🖥️ System Health":
    st.title("🖥️ System Health")

    conn = get_conn()

    # Database stats
    st.subheader("📊 Database")
    db_path = Path(DB_PATH)
    if db_path.exists():
        size_mb = db_path.stat().st_size / (1024 * 1024)
        st.metric("Database Size", f"{size_mb:.1f} MB")

    tables = ["tokens", "scores", "metrics", "safety_checks", "alerts",
              "danger_alerts", "social_mentions", "smart_money_wallets",
              "deployer_history", "narratives"]
    cols = st.columns(5)
    for i, table in enumerate(tables):
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            cols[i % 5].metric(table.replace("_", " ").title(), count)
        except Exception:
            cols[i % 5].metric(table, "N/A")

    # Activity stats
    st.subheader("📈 Activity (Last 24h)")
    tokens_24h = conn.execute(
        "SELECT COUNT(*) FROM tokens WHERE last_updated_at > datetime('now', '-24 hours')"
    ).fetchone()[0]
    alerts_24h = conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE sent_at > datetime('now', '-24 hours')"
    ).fetchone()[0]
    dangers_24h = conn.execute(
        "SELECT COUNT(*) FROM danger_alerts WHERE sent_at > datetime('now', '-24 hours')"
    ).fetchone()[0]

    c1, c2, c3 = st.columns(3)
    c1.metric("Tokens Scanned", tokens_24h)
    c2.metric("Alerts Sent", alerts_24h)
    c3.metric("Danger Signals", dangers_24h)

    # Smart money wallet stats
    st.subheader("💰 Smart Money")
    wallets = conn.execute("SELECT COUNT(*) FROM smart_money_wallets").fetchone()[0]
    trades = conn.execute("SELECT COUNT(*) FROM smart_money_trades").fetchone()[0]
    c1, c2 = st.columns(2)
    c1.metric("Tracked Wallets", wallets)
    c2.metric("Total Trades Recorded", trades)

    # Narratives
    st.subheader("📖 Active Narratives")
    narratives = conn.execute(
        "SELECT name, heat_score, keywords FROM narratives WHERE heat_score > 0.1 ORDER BY heat_score DESC LIMIT 10"
    ).fetchall()
    for n in narratives:
        keywords = json.loads(n["keywords"]) if n["keywords"] else []
        st.write(f"**{n['name']}** (heat: {n['heat_score']:.1f}) — {', '.join(keywords[:5])}")

    conn.close()


# ---------- Helpers ----------
def _fmt_num(val) -> str:
    if val is None:
        return "N/A"
    val = float(val)
    if val >= 1_000_000_000:
        return f"${val / 1_000_000_000:.2f}B"
    if val >= 1_000_000:
        return f"${val / 1_000_000:.2f}M"
    if val >= 1_000:
        return f"${val / 1_000:.1f}K"
    return f"${val:.2f}"
