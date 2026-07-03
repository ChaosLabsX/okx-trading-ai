# TradingAI — Crypto Signal Dashboard + Autonomous OKX Trading Bot

**Repository:** [`ChaosLabsX/trading-ai`](https://github.com/ChaosLabsX/trading-ai) · **Live dashboard:** hosted with GitHub Pages at <https://chaoslabsx.github.io/trading-ai/> (every push to `main` auto-deploys the static site).

TradingAI is a two-part system:

1. **A browser dashboard** (static PWA — no build step, no framework, no backend server) that scans 33 crypto coins on OKX in real time, computes technical signals, shows news, and lets the user ask Claude AI for trade recommendations that can be executed on OKX with one click.
2. **A Python background worker** (`signal_checker.py`) that runs 24/7 on GitHub Actions (triggered every ~5 minutes), detects STRONG BUY signals, **automatically places "Option 3" trades on OKX**, monitors those trades through their whole lifecycle, and reports every event to the user via Telegram.

The two halves share the same signal engine (identical scoring logic implemented twice — JS and Python), the same Supabase project (settings storage + trade tracking), the same OKX account, and the same Telegram bot.

> **⚠️ CURRENT STATE: TEST MODE IS ON.**
> `TEST_MODE = True` in `signal_checker.py` lowers the STRONG BUY bar (score ≥ 1 instead of ≥ 5), skips the reversal-confirmation gate and the AI advisor, and places fixed **$5 USDT** trades (TP 1.5% / SL 2% / trail 1%), one live test trade at a time — worst case ≈ $0.11 per test. Setting `TEST_MODE = False` (one line) restores all production behavior. See [docs/SIGNAL-CHECKER.md](docs/SIGNAL-CHECKER.md#test-mode).

## Documentation map

| Doc | What it covers |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System components, data flow, file map, Supabase tables, secrets & config |
| [docs/DASHBOARD.md](docs/DASHBOARD.md) | The browser app: scanner, signal engine, AI Advisor, trade execution, news, settings/lock screen, PWA |
| [docs/SIGNAL-CHECKER.md](docs/SIGNAL-CHECKER.md) | The Python worker: scan loop, alert rules, auto-trade decision flow, test mode, Telegram messages |
| [docs/OPTION3-TRADE-SYSTEM.md](docs/OPTION3-TRADE-SYSTEM.md) | The Option 3 trade strategy in full detail: order placement, phases, exit monitoring, P&L math |
| [docs/CRON-JOB-ORG.md](docs/CRON-JOB-ORG.md) | How the worker is scheduled: GitHub Actions cron + cron-job.org external trigger |

## Repository layout

```
trading-ai/  (repo root)
├── index.html              # Dashboard UI (single page: scanner, AI advisor, news, modals)
├── app.js                  # All dashboard logic (~2000 lines, vanilla JS)
├── config.js               # Public defaults: coin list, Supabase URL/anon key, refresh timings
├── style.css               # Dashboard styling (dark theme)
├── sw.js                   # Service worker (offline cache for static assets)
├── manifest.json           # PWA manifest
├── site.webmanifest        # PWA manifest (linked from index.html)
├── signal_checker.py       # 24/7 background worker (signals + auto-trade + monitor + Telegram)
├── .github/workflows/
│   └── signal-checker.yml  # GitHub Actions workflow (cron + repository_dispatch triggers)
├── docs/                   # ← this documentation
└── Illustrator/            # Logo source assets (not code)
```

## The five external services

| Service | Used for | Credentials live in |
|---|---|---|
| **OKX** | Market data (public API, no key) + account balance, spot orders, algo orders (private API, HMAC-signed) | Browser: encrypted Supabase settings · Worker: GitHub Secrets |
| **Supabase** | 1) Encrypted API-key storage for the dashboard, 2) `option3_trades` table that both halves write/read | URL + anon key are public in `config.js`; worker uses `SUPABASE_URL`/`SUPABASE_KEY` secrets |
| **Telegram** | All trade/exit notifications (sent **only** by the Python worker — the browser never sends Telegram messages) | Bot token + chat ID |
| **Anthropic (Claude)** | Browser AI Advisor (`claude-sonnet-4-6`, called directly from the browser) and worker trade advisor (`claude-haiku-4-5-20251001`) | Browser: user's key in settings · Worker: `CLAUDE_API_KEY` secret |
| **cron-job.org** | External scheduler that triggers the GitHub Actions workflow every 5 minutes (GitHub's own cron is unreliable) | GitHub PAT stored in the cron-job.org job config |

## Quick "how it trades" summary

1. Every ~5 min, GitHub Actions runs `signal_checker.py` (internally re-scans every 60 s for ~4 min).
2. Each coin gets a score from RSI(1H), MACD, Bollinger %B, volume ratio, and RSI(4H). Score ≥ 5 → STRONG BUY (≥ 1 in test mode).
3. A STRONG BUY must also pass a 30-minute-candle reversal confirmation (green candle + rising RSI + volume ≥ average) — skipped in test mode.
4. Safety rails gate all trading (enforced in production, logged-only in test mode): a **BTC regime filter** blocks dip-buys while BTC is in a 4H downtrend, a **max-3 open trades** cap, and a **circuit breaker** that pauses trading after 3 stop-losses in 24h.
5. Qualified coins are ranked; the top 2 per scan (1 in test mode) go to Claude Haiku — which also sees the bot's **recent live trade results** from Supabase — and it decides TRADE or SKIP and sizes the position (10–30% of the live USDT balance).
6. An approved trade is placed as an **Option 3 trade**: market buy → OCO (take-profit + stop-loss on 50%) → conditional stop-loss (other 50%), so the **full position is SL-protected on OKX 24/7**. The trade is saved to Supabase.
7. Every subsequent run, `monitor_option3_trades()` polls OKX for exits: partial TP hit → the 2nd-half SL is swapped for an active trailing stop (phase 2); SL hit → both halves stop out server-side; the trailing stop exit ends the trade. Every close records its outcome (`exit_reason`, `net_pnl_usdt`, `closed_at`) in Supabase and sends a Telegram message with the **exact USDT profit/loss net of OKX fees**.

## Development notes for the next AI/developer

- There is **no package.json, no bundler, no framework** — the dashboard is plain HTML/CSS/JS served statically. The only Python dependency is `requests` (installed inline in the workflow).
- The signal engine exists **twice** — `generateSignal()` in `app.js` and `generate_signal()` in `signal_checker.py`. They are intentionally kept in sync; if you change scoring rules, change both.
- The browser reaches OKX's *private* API through `corsproxy.io` (OKX has no CORS headers). Public market-data endpoints are called directly.
- `signal_cache.json` (git-ignored) is the worker's alert-deduplication state, persisted between Actions runs via `actions/cache`.
- All Telegram messages deliberately contain **no timestamp line** — Telegram's own message time is used instead.
- Money-affecting logic lives in exactly two places: `executeTrade()` in `app.js` (manual, user-confirmed) and `place_option3_trade()` / `monitor_option3_trades()` in `signal_checker.py` (autonomous). Touch these with care and test with `TEST_MODE = True` first.
