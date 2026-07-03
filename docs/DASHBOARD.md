# Browser Dashboard (`index.html` + `app.js`)

A single-page vanilla-JS app, installable as a PWA. No build step — `index.html` loads `config.js` then `app.js`. All state lives in the global `state` object; persistence is `localStorage` (`LS` helper) plus optional encrypted Supabase cloud storage.

## Startup sequence (`init()`)

1. `loadSettings()` — pull API keys/prefs from `localStorage` into `CONFIG`.
2. Load `notifiedSignals` (alert dedup memory) and scanner symbol list (saved list merged with any new coins added to `CONFIG.DEFAULT_SCANNER`).
3. Register the service worker.
4. **If Supabase is configured** (it is, by default in `config.js`): try auto-unlock with a password cached in `sessionStorage`; otherwise show the **lock screen**. On successful unlock (`loadFromCloud`), decrypted settings overwrite `CONFIG` and `localStorage`, then `loadAppData()` runs.
5. `loadAppData()` — restore last-known USDT balance, fetch all market data, render, start auto-refresh timers, background-sync the OKX balance.

## Panels

### 1. Signal Scanner (primary panel)

- Watches the 33 symbols in `CONFIG.DEFAULT_SCANNER` (user-editable, persisted as `scanner` in localStorage).
- `fetchAllData()` pulls, per symbol: ticker + 1H + 4H + 30m candles — batched 2 symbols at a time with 250 ms gaps to avoid OKX 429s. Funding rate + open interest fetched separately in a non-blocking loop.
- `computeIndicators()` produces: RSI(14) 1H + previous, RSI 4H, MACD (12/26/9), Bollinger %B (20, 2σ), volume ratio (last candle vs 20-bar avg), 30m reversal inputs, and the **signal**.
- **Signal age**: `computeIndicators` walks back up to 10 1H candles re-running the signal to find when the current label began (`signalStartTs`) — so "In Signal" ages are derived from OKX candle timestamps and identical on every device. A 1 s ticker (`startAgeTicker`) keeps the age cells live.
- Table shows: price, 24h %, RSI 1H, RSI 4H, MACD state, BB%, volume ratio, signal badge with score, signal age. Hover filter chips (Buy/Sell), sort dropdown, "Top Pick" banner for the highest score ≥ 2.
- If OKX is unreachable a deterministic **Demo** dataset renders instead (`mockTicker`); demo rows are excluded from alerts and the scanner display.

### 2. Signal engine (`generateSignal()`)

Score components (identical to the Python worker — keep in sync!):

| Indicator | Contribution |
|---|---|
| RSI 1H | ≤20: **+3** · ≤30: **+2** · ≤40: 0 (reason only) · ≥60: **−1** · ≥70: **−2** · ≥80: **−3** |
| MACD | bullish cross: **+2** · bearish cross: **−2** · trend only: **±0.5** |
| Bollinger %B | ≤0.05: **+2** · ≤0.20: **+1** · ≥0.80: **−1** · ≥0.95: **−2** |
| RSI 4H (confirmation) | score>0 & ≤40: **+1** · score<0 & ≥55: **−1** · caution counters: **∓0.5** |
| Volume ratio | ≥1.5× amplifies an existing ±2 signal by **±1** |

Labels: score ≥ 5 → **STRONG BUY**, ≥ 2 → BUY, > −2 → HOLD, > −5 → SELL, else **STRONG SELL**.

> Minor asymmetry vs the worker: the browser gives the volume point only when the score is already ±2, while the worker gives +1 at ≥ 2.0× volume unconditionally. Same intent, slightly different code paths.

### 3. Alerts (`checkSignalAlerts()`)

Browser alerts are **toasts only** — Telegram for STRONG BUY is handled exclusively by the Python worker to avoid duplicates. On the first scan after page load all currently-STRONG-BUY coins are silently marked as already-notified (warm-up). A STRONG BUY without 30m reversal confirmation shows a "waiting for reversal" toast with the failing conditions (`reversalWhyFailed`). `sendTelegramAlert()` exists in the code but is currently unused.

### 4. AI Advisor (`runAiAnalysis()`)

- Calls the Anthropic Messages API **directly from the browser** (`anthropic-dangerous-direct-browser-access` header) with model `CONFIG.CLAUDE_MODEL` (`claude-sonnet-4-6`).
- Before prompting, it fetches the **live** OKX balance and holdings so the AI sees real capital, then builds:
  - `buildSystemPrompt()` — strict trading rules (only recommend BUY with score ≥ 5 and ≥ 2 confirmations, explicit SKIP conditions, confidence levels), risk-profile position sizing (conservative 10% / moderate 20% / aggressive 30% of capital), and the **TRADE tag contract** with Option 3 parameter guidance per volatility tier.
  - `buildPrompt()` — per-coin technical snapshot, derivatives context (funding rate, open interest), portfolio with live P&L, news headlines + sentiment %.
- The response is rendered as markdown; any `[TRADE:{...}]` tags are parsed (`parseTradeActions`) into **action buttons** ("⚡ Option 3 Trade · 🟢 BUY AVAX · TP50% +5% · Trail 3% · SL −8%").

### 5. Trade execution (`showTradeConfirmation()` → `executeTrade()`)

Clicking an action button opens a confirmation modal where amount, partial-TP %, trailing %, and SL % are all **editable** with live price previews. Confirming runs the same Option 3 placement sequence as the worker:

1. Spot market **buy** (`tgtCcy: 'quote_ccy'` — size in USDT).
2. **OCO** conditional sell for 50% (TP trigger + SL trigger in one order — one order so OKX reserves the coin balance once; two separate orders would try to reserve 150% of holdings and get rejected).
3. **Trailing stop** (`move_order_stop`) for the other 50%, activation price = TP price.
4. `saveOption3Trade()` inserts the row into Supabase so the Python worker's monitor takes over from there (break-even SL move + exit Telegram messages happen server-side even with the browser closed).

A non-Option-3 `[TRADE]` sell tag falls back to a simple market sell. See [OPTION3-TRADE-SYSTEM.md](OPTION3-TRADE-SYSTEM.md) for the full lifecycle.

### 6. News panel

RSS feeds (CryptoPanic → CoinTelegraph → Decrypt → CoinDesk → Bitcoin Magazine → The Block) fetched through CORS proxies (`corsproxy.io`, fallback `api.allorigins.win`) — first feed that yields articles wins. Keyword search filters by topic. `guessSentiment()` regex-classifies each headline (pos/neg/neu) and an aggregate "% bullish" sentiment bar renders on top. Demo articles if all feeds fail.

### 7. Balance sync

`fetchOKXBalance()` merges the **unified trading account** and the **funding account** (on Classic accounts spot coins often sit in Funding), dedupes per coin, and keeps the higher USDT figure. Shown in the summary bar; cached in `localStorage` for instant display on next load.

### 8. Settings modal

Sections: Supabase config, Claude API key, Telegram (token/chat ID), OKX keys (Read + Trade, no Withdraw), **Cloud Security** (password + "Save All to Cloud" → encrypt to Supabase `app_settings`), risk profile, auto-refresh interval, scanner export/import JSON, full reset.

## Refresh behavior

- Market data auto-refreshes every `CONFIG.AUTO_REFRESH_INTERVAL` (default 60 s), news every 10 min.
- The `visibilitychange` handler **pauses all timers when the tab is hidden** (saves API calls; the worker covers background monitoring) and refreshes immediately on return.

## PWA (`sw.js`, `site.webmanifest`)

Network-first with cache fallback for GET requests; OKX/Anthropic/API calls are never cached. Static assets pre-cached at install (`tradingai-v1`). This makes the dashboard installable on mobile and usable (read-only, last data) offline.
