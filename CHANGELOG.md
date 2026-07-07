# Changelog

Every meaningful change to the app, newest first. Kept so a future developer (human or AI)
can trace what was done and why without digging through git history.

## 2026-07-07 — Bot Performance panel + portrait lock (dashboard)

- **Bot Performance panel**: new bar-chart button in the header slides in a P&L dashboard
  above the scanner. Lazy-loaded — zero requests at page load (coin speed preserved); the
  first open fetches all closed trades from Supabase once and caches them, so range
  switching (7D / 30D / 90D / All / custom from→to dates) is instant. Shows net P&L
  **after OKX fees** as the headline plus before-fees and estimated-fees columns, trades
  W/L, win rate, profit factor, avg win/loss, a cumulative equity-curve chart, per-coin
  net table, and exit-type counts. Verified live in a browser (Supabase query 200,
  lazy-load confirmed, rendering checked with sample data).
- **Portrait-only lock**: manifests set `"orientation": "portrait"` (installed PWA),
  best-effort `screen.orientation.lock()` at init, and a full-screen "rotate back"
  overlay for phone-sized landscape (desktops unaffected). Verified in the browser.

## 2026-07-07 — Backtesting harness

- New `backtest.py`: replays the PRODUCTION signal + Option 3 exit logic over historical
  OKX candles (free public endpoint, disk-cached, no keys, no orders). Imports the real
  functions from `signal_checker.py` so the tested logic can't drift from the traded logic.
  Flags to A/B any knob: `--score`, `--atr-tp/-sl/-trail`, `--no-regime`, `--no-reversal`,
  `--days`, `--coins`, `--stake`. Conservative fill model (next-candle-open entries,
  SL-first on ambiguous candles, taker fees). Full guide in docs/BACKTESTING.md.
- First real finding (6 majors × 45 days): all 124 STRONG BUY signals were regime-blocked;
  with the filter off they would have netted −$4.35 at PF 0.62 — i.e. the BTC regime
  filter demonstrably saved money during this bear stretch.

## 2026-07-07 — Daily digest + Fear & Greed

- **Daily Telegram report** (`maybe_send_daily_digest`, fires once per UTC day after 08:00):
  bot-alive heartbeat + mode, Fear & Greed, open trades, win rate & net P&L over the last
  100 closed trades, profit factor → current sizing tier, 7-day slice, best/worst coins.
  Doubles as a **dead-man switch** — if the report stops arriving, the pipeline is down
  (expired cron-job.org PAT, broken workflow, etc.). Dedup via `_daily_digest` cache key.
- **Fear & Greed Index** (alternative.me — free, keyless): shown color-coded in the dashboard
  summary bar (refreshes with the news cadence), added to the AI trade-decision prompt with
  contrarian rules (≤ 25 Extreme Fear → dip-buy conditions; ≥ 75 Extreme Greed → cut size
  25–50%, tighter TP), and included in the daily report. Free replacement for the
  CryptoPanic idea after their API went paid.

## 2026-07-06 — Major upgrade round (still in TEST_MODE)

### Telegram messages
- Every "sold" message now shows the **exact USDT profit/loss net of OKX fees** (never just a
  percentage). When OKX won't return a fill price, an estimate marked `~` is shown instead.
- Fill-price lookup made robust: `avgPx` → `actualPx` → the child market order's `avgPx`.
- Removed the `⏰ HH:MM UTC` line from **all** messages (Telegram's native timestamp is used).
- New message types: Fast Reversal (whipsaw), Auto-Trading Paused (circuit breaker).
- Phase-2 exits report the recovered phase-1 profit and the **whole-trade net result**.

### Trade structure (Option 3 hardening)
- **Full-position stop-loss protection**: the 2nd half now gets its own conditional SL at
  placement (instead of a dormant trailing stop) — in a crash both halves stop out server-side
  on OKX even if GitHub Actions is down. When the TP fills, the monitor swaps that SL for an
  immediately-active trailing stop (`_swap_sl2_to_trailing`). New Supabase column: `sl2_id`.
- Whipsaw handling: TP fills then price crashes through the 2nd-half SL within one monitor
  window → detected, closed, reported as `tp_then_sl`.
- Phase-2 exits cancel the counterpart order (no dangling algo orders on OKX).
- Honest failure reporting: if the break-even SL can't be placed, Telegram says so.
- Trade outcomes recorded on close: `exit_reason`, `exit_price`, `net_pnl_usdt`, `closed_at`
  (Supabase migration in docs/ARCHITECTURE.md — **must be run once in the SQL editor**).

### Safety rails (enforced in production, logged-only in TEST_MODE)
- **BTC regime filter**: no dip-buying while BTC is below its 4H EMA-50 with 4H RSI < 45.
- **Open-trade cap**: max 3 concurrent Option 3 trades (`MAX_OPEN_TRADES`).
- **Daily circuit breaker**: 3 stop-loss exits in 24h pauses new trades until the window
  clears (`MAX_SL_PER_DAY`), with a once-per-day Telegram notice.

### AI decision-maker
- Model upgraded Haiku → **Claude Opus 4.8** (`claude-opus-4-8`) with **adaptive thinking**;
  response parser handles thinking blocks; `max_tokens` 2000; ~$0.01–0.02 per decision.
- **ATR-based exits**: TP = 2×ATR(14), SL = 2.5×ATR, trail = 1×ATR from live 1H candles.
- **Support/resistance**: TP pulled 0.5% below the nearest swing-high ceiling, SL pushed
  0.75% below the nearest swing-low floor (`suggest_exit_params`).
- **Code-enforced clamps** regardless of the AI's answer: TP 1.5–10%, SL 2–12%, trail 1–5%
  and always < TP (protects the phase-2 break-even guarantee).
- **Rich context in the prompt**: funding rate & open interest (funding > +0.10% auto-skips
  before the AI is consulted), order-book bid/ask imbalance (top 20 levels), BTC regime values.
- **News veto**: the coin's latest CryptoCompare headlines (verified genuinely coin-tagged)
  go into the prompt — hack/exploit/lawsuit/SEC/delisting/insolvency → SKIP regardless of
  indicators; no headlines is neutral (`fetch_coin_news`).
- **Performance-weighted sizing**: position cap scales with the last-30-trades profit factor
  (PF ≥ 1.5 → 30%, 1.0–1.5 → 22%, < 1.0 → 15%), enforced in code and prompt.
- The AI also sees the bot's recent win/loss record overall and for the specific coin.

### Trade execution
- **Maker-first limit entries**: limit buy 0.05% below market (rounded to the instrument's
  official tick/lot size), 45s wait, then cancel + market fallback — cuts fees + slippage
  roughly in half; partial fills and cancel-races-a-fill handled; `entry_price` now comes
  from real fills when known. Active in test mode too.

### Test mode (current state)
- STRONG BUY bar lowered to score ≥ 1 (production stays ≥ 5) so tests trigger fast.
- Test trades shrunk to $5 with TP 1.5% / SL 2% / trail 1% — worst case ≈ $0.11 per test.
- `TEST_MODE = False` still reverts everything to production behavior in one line.

### Dashboard
- News: three sources fetched in parallel and merged (CryptoCompare News API primary — direct,
  no proxy, keyed; CryptoPanic community-voted sentiment when `CRYPTOPANIC_API_KEY` is set;
  CoinTelegraph + CoinDesk RSS), deduped by title, newest first.
- Risk profile permanently `aggressive`, auto-refresh permanently 1 minute — both removed from
  the Settings UI (fixed in config.js). Also fixed a pre-existing crash in `saveSettings()`
  (referenced a form field that doesn't exist).

### Bug fixes found during verification
- Circuit-breaker Supabase query: timestamp `+00:00` URL-decoded as a space → query always
  failed silently. Now uses `Z` format (verified against the live table).
- Coin quantities could serialize in scientific notation for high-priced coins (BTC) → OKX
  rejection. All monitor order sizes now use fixed 8-decimal formatting.
- CryptoCompare pads thin coin categories with general news → headlines are now verified
  against each article's own tags before reaching the AI.

### Keys / config added
- `CRYPTOCOMPARE_API_KEY` (free, read-only, news scope) in config.js + signal_checker.py.
- `CRYPTOPANIC_API_KEY` placeholder in config.js — **left empty on purpose**: CryptoPanic's
  API turned out to be paid (~$50/week, rejected as not worth it). The integration code stays
  dormant; keyword sentiment is used and trading is unaffected (the AI judges raw headlines).

## Earlier (pre-changelog)
- Initial system: browser dashboard (scanner/AI advisor/news/PWA), Python worker on GitHub
  Actions (signals → Option 3 auto-trades → monitor → Telegram), Supabase persistence,
  cron-job.org scheduling. Documented across README.md and docs/.
