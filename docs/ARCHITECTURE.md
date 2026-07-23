# Architecture

- **GitHub repository:** [`ChaosLabsX/okx-trading-ai`](https://github.com/ChaosLabsX/okx-trading-ai) (branch `main`)
- **Dashboard hosting:** GitHub Pages — the repo root *is* the static site, so every push to `main` deploys the dashboard to <https://chaoslabsx.github.io/okx-trading-ai/>
- **Worker hosting:** GitHub Actions in the same repo (workflow `.github/workflows/signal-checker.yml`)

## System overview

```
                       ┌──────────────────────────────┐
                       │         cron-job.org         │  every 5 min
                       │  POST /repos/…/dispatches    │──────────────┐
                       └──────────────────────────────┘              │
                                                                     ▼
┌──────────────────────┐                              ┌──────────────────────────┐
│   Browser Dashboard  │                              │      GitHub Actions      │
│  (index.html/app.js) │                              │  signal-checker.yml runs │
│                      │                              │    signal_checker.py     │
│  • Signal scanner    │                              │                          │
│  • AI Advisor        │                              │  • scan 38 coins / 60s   │
│  • Manual Option 3   │                              │  • auto-place Option 3   │
│    trade execution   │                              │  • monitor open trades   │
│  • News + sentiment  │                              │  • Telegram alerts       │
└──────┬───────┬───────┘                              └───────┬───────┬──────────┘
       │       │                                              │       │
       │       │        ┌──────────────────────┐              │       │
       │       └───────►│       Supabase       │◄─────────────┘       │
       │                │  app_settings        │                      │
       │                │  option3_trades      │                      ▼
       │                └──────────────────────┘              ┌──────────────┐
       │                                                      │   Telegram   │
       ▼                                                      │  (user chat) │
┌──────────────────────────────────────────────┐              └──────────────┘
│                     OKX                      │
│  public: tickers, candles, funding rate, OI  │
│  private: balance, spot orders, algo orders  │
└──────────────────────────────────────────────┘
```

Two independent programs cooperate through shared external state:

- **Browser dashboard** — for a human watching the market and placing trades manually (with AI help). Runs only while a browser tab is open.
- **Python worker** — the autonomous side. Places and manages trades even when no browser is open. This is the component that generates the Telegram traffic.

They coordinate through the Supabase `option3_trades` table: a trade placed from *either* side is saved there and gets picked up by the worker's monitor on its next run.

## Data flow for one autonomous trade

1. cron-job.org (or GitHub's backup cron) triggers the workflow.
2. `signal_checker.py` scans coins using OKX **public** endpoints (`/api/v5/market/candles`, `/api/v5/market/ticker`).
3. A STRONG BUY passes filters → Claude Opus 4.8 is consulted (production only) → `place_option3_trade()` posts three orders to OKX **private** endpoints.
4. The trade row is inserted into Supabase `option3_trades` with `phase = 1`.
5. A Telegram message confirms the trade ("Trade Already Placed on OKX ✅").
6. On every later run, `monitor_option3_trades()` reads all rows with `phase < 3`, checks OKX algo-order history for triggers, advances the phase, and sends exit Telegram messages with exact USDT P&L.

## Supabase

One Supabase project. The URL and **anon key** ship publicly in `config.js` (this is by design — the anon key is meant to be public; data protection comes from encryption for settings, and row content for trades is not sensitive). The worker authenticates with `SUPABASE_URL` / `SUPABASE_KEY` GitHub Secrets, using the same REST (PostgREST) API.

### Table: `app_settings`

Encrypted cloud storage for the dashboard's API keys. Exactly one row (`id = 'main'`).

| Column | Type | Notes |
|---|---|---|
| `id` | text PK | Always `'main'` |
| `password_hash` | text | SHA-256 of `password + salt` — used to verify the unlock password |
| `encrypted_data` | text | Base64 AES-GCM ciphertext of the settings JSON |
| `iv` | text | Base64 12-byte AES-GCM IV |
| `salt` | text | Random hex, used for both PBKDF2 and the password hash |

The plaintext payload (never stored unencrypted) is: Claude API key, OKX key/secret/passphrase, Telegram token/chat ID. (Risk profile and refresh interval are hard-coded in `config.js` — always `aggressive` / 1 minute — and are no longer stored or shown in Settings; older cloud rows may still contain them, and they're simply ignored on load.) Crypto: PBKDF2 (SHA-256, 100 000 iterations) → AES-GCM-256, all via the browser Web Crypto API (`encryptJSON`/`decryptJSON` in `app.js`). **The password is never stored** — only its salted hash; the worker has no access to this table and doesn't need it.

### Table: `option3_trades`

The shared trade-tracking table. Written by `saveOption3Trade()` (`app.js`) and `_save_option3_trade()` (`signal_checker.py`); read/updated only by the worker.

| Column | Type | Notes |
|---|---|---|
| `id` | text PK | The OKX **OCO algo order ID** (doubles as trade ID) |
| `symbol` | text | e.g. `POL-USDT` |
| `entry_price` | numeric | Market price at buy time |
| `partial_tp_id` | text | OCO algo ID (same as `id`) |
| `sl_id` | text | Phase 1: same OCO algo ID. Phase 2: empty (new format) or break-even SL algo ID (fallback/legacy) |
| `sl2_id` | text | 2nd-half stop-loss algo ID (full position SL-protected in phase 1) |
| `trailing_id` | text | Trailing-stop algo ID — empty at placement, set by the monitor when the TP fires |
| `amount_usdt` | numeric | USDT spent on the market buy |
| `sz_half` | numeric | Coin quantity of one half-position (50% of fill, with 0.9985 fee haircut) |
| `partial_tp_pct` | numeric | Take-profit % |
| `sl_pct` | numeric | Stop-loss % |
| `trailing_pct` | numeric | Trailing callback % |
| `phase` | int | **1** = waiting for TP or SL · **2** = TP hit, trailing stop riding · **3** = closed |
| `exit_reason` | text | `tp_trail` · `sl` · `break_even` · `tp_then_sl` (whipsaw) · `cancelled` · `error` |
| `exit_price` | numeric | (Average) exit fill price |
| `net_pnl_usdt` | numeric | Whole-trade net P&L incl. fees — feeds the circuit breaker and the AI's history context |
| `closed_at` | timestamptz | When the monitor closed the trade |

Rows are never deleted — `phase = 3` marks a finished trade. The monitor queries `?phase=lt.3`.

**Required migration** (run once in Supabase → SQL Editor; the code degrades gracefully but loses 2nd-half tracking, the circuit breaker, AI history, and the trade journal without it):

```sql
alter table option3_trades
  add column if not exists sl2_id        text,
  add column if not exists exit_reason   text,
  add column if not exists exit_price    numeric,
  add column if not exists net_pnl_usdt  numeric,
  add column if not exists closed_at     timestamptz,
  -- Trade journal (learning loop)
  add column if not exists entry_context jsonb,
  add column if not exists followup      jsonb,
  add column if not exists followup_at   timestamptz;

-- Setups the AI declined — the other half of the mistake ledger
create table if not exists skipped_setups (
  id            bigserial primary key,
  symbol        text not null,
  price         numeric,
  reason        text,
  entry_context jsonb,
  created_at    timestamptz default now(),
  followup      jsonb,
  followup_at   timestamptz
);

-- Distilled-journal learning pass (learn.py). One row per pass. `cohorts` holds the
-- code-computed statistics, `learned_block`/`proposals` hold Opus's judgement. The
-- code degrades silently (prints a one-line note) if this table is absent.
create table if not exists learned_rules (
  id              bigserial primary key,
  created_at      timestamptz default now(),
  trades_analyzed int,
  cohorts         jsonb,
  summary         text,
  learned_block   text,
  proposals       jsonb
);
```

### The trade journal (`entry_context` / `followup` / `skipped_setups`)

The AI's learning loop. Three parts:

- **`entry_context`** (jsonb, written at placement by `_build_entry_snapshot()`) — the market picture the decision was made on: score + reasons, RSI 1H/4H, MACD, BB %B, volume ratio, ATR, funding, order-book ratio, support/resistance, BTC regime, Fear & Greed, and the TP/SL/trail chosen. Every value was already computed to make the trade; before this it was discarded. ~600 bytes/trade.
- **`followup`** (jsonb, written ~24 h after close by `grade_journal_followups()`) — what price did *after* the exit, as a verdict: `shakeout` (stopped us out, then reached our TP anyway → SL too tight) · `good_save` (kept falling → stop earned its keep) · `partial_recovery` · `flat_after_stop` · `left_money` (ran well past our trailing exit) · `well_timed` · `fair_exit`. **This is what makes losses useful** — `shakeout` and `good_save` are both stop-losses, identical in a P&L column, and imply opposite fixes.
- **`skipped_setups`** — every AI `[SKIP]` (and funding auto-skip) with the same snapshot, graded the same way: `missed_win` (would have hit target — too cautious) · `good_skip` (would have stopped out) · `neutral_skip`. Mechanical `Option3Preflight` rejections are deliberately **not** logged — they're size limits, not judgments, and would pollute the AI's record.

`_trade_history_context()` and `_skip_history_context()` render this back into every prompt, with code-computed patterns ("3 of 8 trades were SHAKEOUTS → widen slPct") rather than model-inferred ones. Below `JOURNAL_MIN_SAMPLES` (10) closed trades the prompt explicitly labels the history **anecdote, not statistics**, to stop the AI over-generalizing from noise. Note this is in-context memory re-read each decision — the model itself is never retrained.

### The learning pass (`learn.py`)

The per-trade journal above shows the AI only the **last ~30** trades — a rolling
window that plateaus (trade #500 sees about as much as trade #40). The learning pass
is the long-horizon complement: it reads the **entire** graded history and distils it
into conditional statistics that keep compounding past that window.

Same discipline as the journal, drawn harder: **code computes every number** (cohort
counts and profit factors are exact aggregations in `_compute_cohorts()`), and Opus
only **judges** the pre-computed cohorts — which look like a real parameter problem vs
noise, and what bounded change follows. It never estimates a statistic.

- Runs once per Actions run, and only after `LEARN_TRIGGER_NEW_TRADES` (25) newly
  graded trades since the last pass — so at real volume it fires roughly monthly, on
  evidence, not on a clock. Wrapped in `try/except` so it can never take down the loop.
- Only cohorts of `LEARN_MIN_COHORT` (25) trades or more are ever shown to the model —
  the primary guard against manufacturing a rule from a thin sample. The model is
  additionally forbidden, in its system prompt, from ever proposing a coin blacklist.
- **Two outputs.** `learned_block` (statistics only) is stored and, once you opt in,
  injected into the live trade prompt. `proposals` (parameter changes) are sent to
  **Telegram for you to approve and apply by hand** — never auto-applied.
- **Injection is OFF by default.** The pass reports and stores from day one, but nothing
  reaches a live decision until you set the `LEARN_INJECT=1` env var / Actions secret —
  after you've read a couple of runs and trust the statistics. It reuses `CLAUDE_MODEL`
  (Opus 4.8): this is a bounded single-turn judgement, not the long-horizon work a pricier
  model would earn its cost on.
- Requires the `learned_rules` table (migration above); degrades silently without it.

> Note: because `partial_tp_id` and `sl_id` share one OCO order ID in phase 1, the monitor distinguishes "TP fired" from "SL fired" by comparing the actual fill price to `entry_price` (fill above entry = TP side, otherwise SL side).

## Configuration & secrets

### Browser (`config.js` + Settings modal)

`config.js` contains only public/non-secret defaults: Supabase URL + anon key, the coin list (`DEFAULT_SCANNER`), refresh intervals, Claude model name, risk profile. Secret keys are **empty** in `config.js`; the user enters them in the Settings modal, and they are stored:

- in `localStorage` (`apiKeys`, `prefs`, `supabaseCfg` keys) for convenience, and
- optionally encrypted in Supabase `app_settings` ("Save All to Cloud"), which enables the password lock screen on every page load.

### Worker (GitHub Secrets, injected in `signal-checker.yml`)

| Secret | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram notifications |
| `OKX_API_KEY`, `OKX_SECRET_KEY`, `OKX_PASSPHRASE` | OKX private API (needs **Read + Trade**, never Withdraw) |
| `CLAUDE_API_KEY` | Claude Opus 4.8 trade advisor (auto-trade silently skips if unset) |
| `SUPABASE_URL`, `SUPABASE_KEY` | Trade persistence — without these, trades still get placed but the monitor can't track them (the trade-confirmation Telegram message warns loudly about this) |

## OKX API usage

- **Public** (no auth): `/market/ticker`, `/market/candles` (1H/4H/30m — now including highs/lows for ATR and support/resistance), `/public/funding-rate` + `/public/open-interest` (browser for the manual AI prompt AND worker for auto-trade context), `/market/books` (worker — order-book bid/ask imbalance for top candidates), `/public/instruments` (worker — tick/lot sizes so limit entries are never rejected for precision).
- **Private** (HMAC-SHA256-signed, ISO timestamp): `/account/balance`, `/asset/balances` (browser only — funding wallet), `/trade/order` (limit + market buys, market sells; also polled by order ID for limit-fill status and exact fill prices), `/trade/cancel-order` (limit-entry timeout fallback), `/trade/order-algo` (`oco` for the TP+SL order, `conditional` for single-leg SLs, `move_order_stop` for trailing stops), `/trade/cancel-algos`, `/trade/orders-algo-history` (exit detection — queried per `ordType`, so an OCO id must be looked up as `oco`).
- The **browser** must route private calls through `https://corsproxy.io/?<url>` because OKX sends no CORS headers; two attempts with re-signing on retry (`okxProxyFetch`). The **worker** calls OKX directly.
- OKX signature quirk (worker): the POST body must be the exact compact JSON string used in the HMAC pre-hash — `json.dumps(body, separators=(',', ':'))` — see `_okx_post()`.
- Assumed taker fee: `OKX_FEE_RATE = 0.001` (0.1%), used for net-P&L math in Telegram messages.

## GitHub Actions workflow (`signal-checker.yml`)

- Triggers: `schedule` (`*/5 * * * *`, backup), `workflow_dispatch` (manual), `repository_dispatch` type `run-signal-checker` (fired by cron-job.org — see [CRON-JOB-ORG.md](CRON-JOB-ORG.md)).
- `concurrency: signal-checker` with `cancel-in-progress: false` — overlapping triggers queue instead of double-running (prevents duplicate Telegram alerts and duplicate trades).
- `timeout-minutes: 6` (script self-exits after ~4 min).
- `signal_cache.json` (alert dedup state) is restored/saved with `actions/cache` around the run; saved `if: always()` with a unique key per run ID, restored by prefix.
