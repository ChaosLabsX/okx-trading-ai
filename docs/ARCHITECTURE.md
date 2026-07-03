# Architecture

- **GitHub repository:** [`ChaosLabsX/trading-ai`](https://github.com/ChaosLabsX/trading-ai) (branch `main`)
- **Dashboard hosting:** GitHub Pages — the repo root *is* the static site, so every push to `main` deploys the dashboard to <https://chaoslabsx.github.io/trading-ai/>
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
│  • AI Advisor        │                              │  • scan 33 coins / 60s   │
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
3. A STRONG BUY passes filters → Claude Haiku is consulted (production only) → `place_option3_trade()` posts three orders to OKX **private** endpoints.
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

The plaintext payload (never stored unencrypted) is: Claude API key, OKX key/secret/passphrase, Telegram token/chat ID, risk profile, refresh interval. Crypto: PBKDF2 (SHA-256, 100 000 iterations) → AES-GCM-256, all via the browser Web Crypto API (`encryptJSON`/`decryptJSON` in `app.js`). **The password is never stored** — only its salted hash; the worker has no access to this table and doesn't need it.

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

**Required migration** (run once in Supabase → SQL Editor; the code degrades gracefully but loses 2nd-half tracking, the circuit breaker, and AI history without it):

```sql
alter table option3_trades
  add column if not exists sl2_id       text,
  add column if not exists exit_reason  text,
  add column if not exists exit_price   numeric,
  add column if not exists net_pnl_usdt numeric,
  add column if not exists closed_at    timestamptz;
```

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
| `CLAUDE_API_KEY` | Claude Haiku trade advisor (auto-trade silently skips if unset) |
| `SUPABASE_URL`, `SUPABASE_KEY` | Trade persistence — without these, trades still get placed but the monitor can't track them (the trade-confirmation Telegram message warns loudly about this) |

## OKX API usage

- **Public** (no auth): `/market/ticker`, `/market/candles` (1H/4H/30m), `/public/funding-rate`, `/public/open-interest` (the last two only from the browser, for the AI prompt).
- **Private** (HMAC-SHA256-signed, ISO timestamp): `/account/balance`, `/asset/balances` (browser only — funding wallet), `/trade/order` (market buy/sell), `/trade/order-algo` (OCO `conditional` + `move_order_stop`), `/trade/cancel-algos`, `/trade/orders-algo-history` (exit detection), `/trade/order?instId=&ordId=` (exact fill price lookup).
- The **browser** must route private calls through `https://corsproxy.io/?<url>` because OKX sends no CORS headers; two attempts with re-signing on retry (`okxProxyFetch`). The **worker** calls OKX directly.
- OKX signature quirk (worker): the POST body must be the exact compact JSON string used in the HMAC pre-hash — `json.dumps(body, separators=(',', ':'))` — see `_okx_post()`.
- Assumed taker fee: `OKX_FEE_RATE = 0.001` (0.1%), used for net-P&L math in Telegram messages.

## GitHub Actions workflow (`signal-checker.yml`)

- Triggers: `schedule` (`*/5 * * * *`, backup), `workflow_dispatch` (manual), `repository_dispatch` type `run-signal-checker` (fired by cron-job.org — see [CRON-JOB-ORG.md](CRON-JOB-ORG.md)).
- `concurrency: signal-checker` with `cancel-in-progress: false` — overlapping triggers queue instead of double-running (prevents duplicate Telegram alerts and duplicate trades).
- `timeout-minutes: 6` (script self-exits after ~4 min).
- `signal_cache.json` (alert dedup state) is restored/saved with `actions/cache` around the run; saved `if: always()` with a unique key per run ID, restored by prefix.
