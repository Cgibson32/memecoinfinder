# AlphaScanner - Meme Coin Research Agent

An autonomous Python agent that continuously monitors multiple blockchains and social platforms to discover high-potential meme coins **before they pump**. It scores tokens on a composite 0-100 scale, filters out scams and rug pulls, and pushes tiered alerts to Telegram with actionable intel.

**Core philosophy:** Speed + Safety + Credibility. Find tokens early, but NEVER surface a token that hasn't passed safety and credibility checks.

## Architecture

```
                    ┌─────────────────┐
                    │   Telegram Bot   │◄── User Commands
                    │  (Alerts + Cmds) │──► Push Alerts
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │   Orchestrator   │
                    │    (main.py)     │
                    └────────┬────────┘
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
    ┌─────▼─────┐    ┌──────▼──────┐    ┌──────▼──────┐
    │ Collectors │    │  Analyzers   │    │   Storage   │
    │            │    │              │    │             │
    │ DEXScreener│    │ Safety(GoPlus│    │ SQLite DB   │
    │ GeckoTerm  │    │ Volume+Wash │    │ TTL Cache   │
    │ Reddit     │    │ Holders     │    │             │
    │ Twitter/CT │    │ Smart Money │    └─────────────┘
    │ Telegram   │    │ Deployer    │
    │ Ggl Trends │    │ Narrative   │    ┌─────────────┐
    │ Market Ctx │    │ Insider Det │    │  Dashboard   │
    └────────────┘    │ Sniper Det  │    │ (Streamlit)  │
                      │ Exit Detect │    │  5 pages     │
                      │ Backtester  │    └─────────────┘
                      │ Scorer(8wt) │
                      └─────────────┘
```

## Features

- **8-Component Scoring**: Volume, Social, Holders, Safety, Liquidity, Smart Money, Narrative, Credibility
- **Smart Money Tracking**: Self-improving wallet database that tracks historically successful traders
- **Safety Gate**: GoPlus Security API integration — honeypot, mint authority, tax, ownership checks
- **Liquidity Lock Detection**: Check if LP is locked via Team Finance/Unicrypt
- **Deployer History**: Detect serial rug-pullers by analyzing deployer wallet history
- **Insider Detection**: Cluster wallets by funding source to find deployer-funded insiders
- **Wash Trade Detection**: Identify fake volume from transaction pattern analysis
- **Sniper Bot Detection**: Flag tokens with coordinated launch buying
- **Narrative Alignment**: Boost tokens riding trending themes (AI, political, culture, etc.)
- **Exit/Danger Alerts**: Monitor alerted tokens for LP removal, holder dumps, price crashes
- **Market Context**: Adaptive thresholds based on BTC trend and Fear & Greed Index
- **Backtesting**: Validate scoring accuracy — track hit rate of past alerts
- **Streamlit Dashboard**: 5-page dashboard with leaderboard, deep dive, alert history, danger signals, system health

## Prerequisites

- Python 3.11+
- A Telegram bot (created via [@BotFather](https://t.me/BotFather))
- Your Telegram chat ID

## Setup

### 1. Clone and install

```bash
git clone <repo-url>
cd alpha-scanner
python -m venv venv
source venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
TELEGRAM_CHAT_ID=your_chat_id
```

### 3. Create a Telegram Bot

1. Open Telegram, search for `@BotFather`
2. Send `/newbot` and follow the prompts
3. Copy the bot token into `.env`

### 4. Get your Chat ID

1. Send any message to your bot
2. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Find `"chat":{"id":` in the response — that's your chat ID

### 5. (Optional) Telegram Group Monitoring

For monitoring alpha groups via Telethon:

1. Go to https://my.telegram.org
2. Create an application to get `API_ID` and `API_HASH`
3. Add to `.env`:
   ```env
   TELEGRAM_API_ID=12345678
   TELEGRAM_API_HASH=your_hash_here
   TELEGRAM_ALPHA_GROUPS=group1,group2
   ```

## Running

### Scanner (main process)

```bash
python main.py
```

### Dry-run mode (no Telegram, prints to console)

```bash
python main.py --dry-run
```

### Dashboard (separate process)

```bash
streamlit run dashboard.py
```

## How Scoring Works

Each token is scored on 8 weighted components (0-100 each):

| Component | Weight | Source |
|-----------|--------|--------|
| Volume | 0.15 | DEXScreener — acceleration, buy pressure, sustainability |
| Social | 0.12 | Reddit, Twitter, Telegram, Google Trends |
| Holders | 0.12 | Holder count growth, distribution health |
| Safety | 0.13 | GoPlus — honeypot, taxes, mint, ownership, LP lock |
| Liquidity | 0.08 | Liquidity depth and ratio |
| Smart Money | 0.15 | Historically successful wallet activity |
| Narrative | 0.10 | Alignment with trending themes |
| Credibility | 0.15 | Deployer history, insider detection, sniper analysis |

**Bonuses**: Dual-source trending, volume-social correlation, Google Trends spike, organic social signal, rising score momentum.

**Penalties**: Scam name patterns, extreme holder concentration, wash trading, unlocked LP, sniper bots, insider wallets.

**Gates**: Honeypot detected, serial rug-pull deployer, insufficient liquidity, too few transactions.

**Alert Tiers**:
- 🔴 CRITICAL (80+): Immediate push with full details
- 🟡 WATCH (60-79): Condensed batch every 5 minutes
- 🟢 RADAR (40-59): Logged for review, included in daily summary

## Configuration

All settings can be overridden via `.env`. See `.env.example` for the full list.

Key settings:
- `ACTIVE_CHAINS`: Comma-separated chains to monitor (default: solana,ethereum,base,bsc)
- `MIN_LIQUIDITY_USD`: Minimum liquidity filter (default: $5,000)
- `MAX_AGE_HOURS`: Skip tokens older than this (default: 72)
- `WEIGHT_*`: Scoring weights (must sum to 1.0)
- `EXIT_*`: Danger alert thresholds

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/top` | Top 10 tokens by score |
| `/watchlist` | Tokens on WATCH tier |
| `/token <address>` | Full score breakdown |
| `/status` | Agent health |
| `/stats` | 24h stats + backtesting |
| `/market` | BTC trend + Fear & Greed |
| `/dangers` | Active danger signals |
| `/settings` | Current configuration |
| `/pause` / `/resume` | Toggle alerts |

## Troubleshooting

**"TELEGRAM_BOT_TOKEN not set"**: Make sure `.env` exists and has your bot token.

**"DEXScreener API unreachable"**: Check your internet connection. The scanner needs access to `api.dexscreener.com`.

**No tokens appearing**: Wait 30+ seconds for the warmup period. Check logs for collector errors.

**Rate limit warnings**: The scanner respects API limits. If you see frequent 429 errors, reduce polling intervals in `.env`.

**Dashboard shows no data**: Make sure the scanner has been running and `alpha_scanner.db` exists in the same directory.

## Cost

**$0/month** — All APIs used are free tier:
- DEXScreener (300 req/min, no auth)
- GeckoTerminal (30 req/min, no auth)
- GoPlus Security (30 req/min, no auth)
- Reddit JSON API (no auth)
- Google Trends via pytrends (rate limited)
- CoinGecko free tier (30 req/min)
- alternative.me Fear & Greed (no auth)
- Nitter RSS (no auth)

## License

MIT
