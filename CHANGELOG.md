# Changelog

Every meaningful change to the app, newest first. Kept so a future developer (human or AI)
can trace what was done and why without digging through git history.

## 2026-07-29 — The stop floor was widened, measured properly, and put back

`SL_BOUNDS` went 2.0 → 8.0 and back to 2.0 the same day. Net code change is a
comment. The reasoning is the deliverable.

**The case for widening**, from 59 historical `RSI<=30 + at lower BB` signals
across 6 coins: a −2.8% stop was hit on 24% of them, and most recovered
afterwards. ADA on 2026-07-27 was exactly that — stopped at −2.9%, back above
its original target within a day.

**Why that was not enough.** The test only asked *does price reach +2% before
−X%*. It modelled none of the machinery that actually determines a trade's P&L:
the 50% partial TP, the trailing stop on the remainder, fees, or the BTC regime
gate that blocks ~88% of signals before they are traded. Replaying the full
engine over 90 days × 38 coins via `backtest.py`:

| `SL_BOUNDS` | net P&L | PF | max DD | avg hold |
|---|---|---|---|---|
| **(2, 12)** | **−6.50** | **0.63** | 11.27 | 26h |
| (8, 12) | −9.86 | 0.60 | 16.38 | 59h |
| no stop | **−44.53** | 0.22 | 56.99 | 296h |

Wider stop, worse result — and the reason is structural, not incidental. Wins
exit on a partial TP plus a trail and average about +2; an 8% stop loses −8.19.
That is ~1:4 against, needing an ~80% win rate to break even where the replay
managed 70%. Widening a stop without widening the target does not give a trade
room, it just makes every loss bigger.

Reverted not because 10 trades prove 2 beats 8 — at that sample the gap is
noise — but because the evidence used to justify the change did not survive the
full replay, and absent a good reason to change, don't.

- **The no-stop row is the one result here that is not sample-size dependent.**
  3 of 8 positions never exited, average age 30.5 days, marked to market at
  −56.99. That is a mechanism — capital locked while the scanner finds signals it
  cannot take — not a coin flip. It also freezes the journal: a trade that never
  closes never gets an `exit_reason` and is never graded, so the learning loop
  built over the last two releases goes silent.
- **Bounds are now interpolated into the system prompt** rather than restated by
  hand. The prompt said `slPct 2–12` while the constant said 8, which would have
  had Claude reasoning about a stop that did not exist — the same defect class as
  the 4H label below. It now tracks the constants automatically, which is what
  made this revert a one-line change instead of two.
- **`backtest.py --no-sl`** added: places trades with no stop and holds to TP or
  end of data, marked to market. Positions still open at the end are reported
  with their count and average age, because an underwater position left open is
  a loss you have not booked, not a loss you have avoided.
- **Fixed: `backtest.py` crashed at the report line on a default Windows
  console.** cp1252 cannot encode `≥`, `·`, `→` or `⚠`, so the process died
  *after* the fetch and the entire replay had run. `sys.stdout.reconfigure(...)`
  in `main()` widens the stream once instead of hunting glyphs.
- **The finding that outranks stop placement: every configuration loses money**
  over those 90 days, PF 0.60–0.63, 10 trades. Ten trades cannot establish that
  either, but it is a reason to treat the strategy as unproven and keep sizes
  small rather than tuning exits on a system whose edge has not been shown.

## 2026-07-29 — Two coins fell together and the bot called it two opinions

Three trades (FET, POL, ADA) opened on the same setup within one session and all
three hit their stops for a combined −$1.25. Two separate defects surfaced.

- **The AI was being told the opposite of the truth.** `generate_signal()`
  labelled `rsi_4h <= 40` as *"higher-TF uptrend confirmed"* and awarded +1.
  RSI 40 on the 4H is the higher timeframe being **weak**. The mirrored branch
  had the same inversion (`rsi_4h >= 55` → *"downtrend confirmed"*). This was not
  cosmetic: the reasons list is pasted verbatim into the Opus prompt as
  `Confirmed by: ...`, so every one of the three losers told the model the 4H
  agreed while it was falling. Labels are now `4H oversold as well` /
  `4H still elevated`, in `signal_checker.py` and the dashboard's `app.js` copy.
  **Scoring is deliberately unchanged** — whether stacked-oversold is confluence
  or a falling knife is an empirical question, `rsi_4h` is in every entry
  snapshot, and the journal can answer it at ~30 graded trades. Reproducing the
  published `[+5.5]` score for the real FET alert is the regression test.
- **`correlation_block()`: concurrency is exposure.** `MAX_OPEN_TRADES = 3`
  counts positions, not bets. The cap was never reached, so nothing stopped one
  bet being placed three times.
- **Average correlation was built, measured, and thrown out.** The first version
  used Pearson r on hourly returns at a 0.75 cap. Against the real candles it
  would have waved all three trades through — FET/POL is only +0.394 on average.
  But on hours BTC fell >0.4% that same pair is **+0.844**, and 83–100% of
  hard-down hours had both coins of every pair red. Average r measures the market
  you are not afraid of. The shipped guard conditions on down bars (~50 of 99
  survive; conditioning on hard-down hours only leaves ~6, which is noise) and
  takes the max of both directions so the verdict is order-independent. All six
  FET/POL/ADA orderings now block.
- **`CORRELATION_MAX = 0.50` is a judgement call and is documented as one.** In
  the current watchlist nearly every pair clears it, so the guard effectively
  means "one open position at a time". Recorded as the finding it is rather than
  tuned until it looked comfortable.
- Fails **open** on missing or short history, printing why — `MAX_OPEN_TRADES`
  is still the hard backstop and a watchlist change must not halt trading.
  Logged-only in `TEST_MODE`, matching the existing rails.

## 2026-07-27 — The journal now has to prove a pattern before prescribing one

The learning loop was telling the AI to retune stops on a single data point. Its
pattern block fired on `if shakeouts:` — **one** shakeout in thirty trades emitted
`PATTERN: ... Consider a wider slPct on similar setups`. A feedback loop that acts
on n=1 doesn't get better over time; it chases noise while looking like it's
learning, and it does so with real money.

- **Significance gate** (`_wilson_lower()`, `_pattern_is_real()`): a prescriptive
  `PATTERN:` line now requires the Wilson 95% lower bound on the rate to clear
  `JOURNAL_PATTERN_NULL_RATE` (0.15), over at least `JOURNAL_MIN_SAMPLES` graded
  trades. At n=30 that's 9 shakeouts, not 1. Hand-rolled, no new dependency —
  the worker still needs only `requests`. `learn.py` already held this line with
  `LEARN_MIN_COHORT = 25`; the per-trade journal did not.
- **Counts survive, directives don't.** Below the bar the model still sees
  "2 of 14 graded trades were shakeouts" as context, and the system prompt now
  says plainly that a bare count is *no evidence at all* — not a weak pattern to
  half-act on. Without that line the gate would have been cosmetic.
- **Bug found while gating it: rates were diluted by ungraded trades.** The
  denominator was every closed trade, including those closed inside the last 24 h
  that have no verdict yet — counting "not graded" as "not a shakeout". Rates are
  now over graded trades only.
- **Skips judged as a two-way split.** `missed >= 3 and missed > good` called
  3-vs-2 a PATTERN. Now: among skips that actually *resolved* (`neutral_skip`
  decided nothing either way), the missed/good split must be distinguishable from
  a coin flip. `_skip_history_context()` returns `(text, evidence)`.
- **`evidence`: the decision's provenance** — journal sample size, which
  directives fired, whether the `learn.py` block was injected, the PF and cap in
  force. Stored inside the existing `entry_context` jsonb on both trades and
  skips, so **no migration**. This is what makes "did the journal help?"
  answerable later instead of permanently unknowable: you cannot compare
  decisions made with more history against ones made with less unless you
  recorded which was which.
- `ai_trade_params()` takes an `evidence_out` dict rather than returning a third
  value — it has six return points and a scar from the last arity change (the
  `no text block` branch, where a bare `return None` took down a whole Actions
  run). Widening all six is the same trap.
- Verified by exercising the real functions against fabricated Supabase
  responses: 1-of-30 stays silent, 12-of-30 fires, 5-of-5 never fires, ungraded
  rows don't dilute, and every degraded path still returns the right arity.

## 2026-07-17 — Trade journal: the AI can now learn from its losses (and its skips)

Goal: let Claude Opus learn from earlier mistakes. It already saw *that* a trade lost
(`_trade_history_context`), never *why* — and the conditions were computed at decision
time and then thrown away. Three additions close that loop:

- **Entry snapshot** (`entry_context` jsonb, `_build_entry_snapshot()`): freezes the market
  picture every decision was made on — score/reasons, RSI 1H+4H, MACD, BB %B, volume, ATR,
  funding, order-book ratio, S/R, BTC regime, Fear & Greed, chosen TP/SL/trail. ~600 bytes,
  zero extra API calls.
- **Post-exit verdict** (`followup` jsonb, `grade_journal_followups()`): ~24 h after a trade
  closes the worker fetches the candles since the exit and grades it — `shakeout` (stopped us
  out then hit our TP anyway → SL too tight) · `good_save` (kept falling → stop earned its
  keep) · `left_money` (ran past our trailing exit) · `well_timed` · `fair_exit` etc.
  **This is what makes a loss teachable**: `shakeout` and `good_save` are identical in a P&L
  column and imply opposite fixes.
- **Skip ledger** (`skipped_setups` table): every AI `[SKIP]` logged with the same snapshot and
  graded the same way (`missed_win` / `good_skip` / `neutral_skip`) — because refusing a good
  trade is a mistake that never shows up in P&L. Mechanical `Option3Preflight` size rejections
  are deliberately not logged (limits, not judgments). `ai_trade_params()` now returns
  `(params, skip_reason)`.
- Both histories are rendered back into every prompt with **code-computed** patterns ("3 of 8
  were SHAKEOUTS → widen slPct"), never model-inferred ones. Under `JOURNAL_MIN_SAMPLES` (10)
  closed trades the prompt labels the data **anecdote, not statistics** and forbids blacklisting
  a coin or jumping size over one or two results — the main over-fitting risk at this sample size.
- Grading runs once per Actions run, ≤5 rows, public candles only. Degrades silently if the
  migration isn't run (`_save_option3_trade` now strips any missing optional column).
- **Requires the updated SQL migration in docs/ARCHITECTURE.md** (new columns + `skipped_setups`).

## 2026-07-17 — CRITICAL: small trades left unprotected positions, silently

User found HYPE on their OKX account they never authorised: two buys (~$10 each,
07/17 00:43 and 05:46), no TP/SL orders, no Telegram, no Supabase row. Not a
compromise — the bot's own worker, failing in the worst possible way.

- **Root cause: an Option 3 position is sold in two halves, and each half must clear
  the instrument's `minSz`.** A $10 HYPE trade buys 0.1617 HYPE; each half is 0.0808,
  under HYPE's 0.1 minimum. OKX **accepts the buy** and only rejects the protective
  orders afterwards → `_okx_post` raised → the coins were already bought → **naked
  position with no stop loss**. Only HYPE ($11.79 min) and ZEC ($10.63) breach this at
  the $10 floor; the other 36 coins are unaffected.
- **Three failures compounded it:** (1) no size pre-check, (2) `run_scan` swallowed the
  exception into `trade_result = 'error'` and Pass 3 only ever notified on success, so
  the user was never told, and (3) no Supabase row meant the symbol never entered
  `active_symbols` — so the coin stayed eligible and **re-bought on the next signal**,
  which is why HYPE was purchased twice.
- **Fixes:** a pre-flight `minSz` check (`Option3Preflight`) rejects an unviable trade
  **before any money moves**, treated as a skip; if a protective order fails *after* the
  entry fills, `_abort_unprotected()` cancels any placed algos, market-sells the position
  straight back, and sends a Telegram either way; `'error'` outcomes now always notify.
  Same pre-check added to the browser's `executeTrade()`. `_fetch_instrument_spec()` now
  also returns `minSz`.
- Verified with mocked-API tests covering all four paths: $10 HYPE blocked pre-buy, $15
  HYPE trades normally, post-buy rejection unwinds + alerts, and a failed unwind escalates.

## 2026-07-15 — CRITICAL: the OCO take-profit leg was never placed

Caught by inspecting the first real production trade (TAO, $16.50 @ 194.6): OKX showed
**both** algo orders as SL-only at 189.1, no TP anywhere, and price had already run
through the intended TP (199.85) without selling.

- **Root cause:** the TP+SL order was sent with `ordType: 'conditional'`. Per OKX's API
  docs, a `conditional` order given both TP and SL params performs *"only stop-loss logic
  … take-profit logic will be ignored"* — accepted with a success response, TP silently
  dropped. Both legs require **`ordType: 'oco'`** (same parameters otherwise).
  **Impact:** no trade could ever take partial profit, arm the trailing stop, or reach
  phase 2 — every Option 3 trade could only ever exit at its stop loss. Present in
  `place_option3_trade()` (worker) *and* the mirrored `executeTrade()` (browser).
- `orders-algo-history` is queried per `ordType`, so OCO lookups now use `oco`, with a
  `conditional` fallback (`OCO_ORD_TYPES`) so trades placed before this fix stay
  monitorable. Also fixed `_phase1_pnl()`, which looks up the OCO id and would otherwise
  have dropped the "phase 1 profit / whole-trade net" lines from phase-2 Telegrams.
- **Entry price now comes from the real market fill.** The market fallback set
  `entry_price` to the *signal-time ticker* (`remaining / price`), but it runs ~45 s
  later, so entry was recorded as exactly 194.6 on the TAO trade — an estimate, not a
  fill. It now reads the order's `avgPx` (falling back to the ticker only if OKX returns
  nothing). This anchors the TP/SL triggers and all P&L to reality, and closes a latent
  bug where a worse-than-ticker fill made the two half-sells exceed actual holdings and
  get rejected.
- Verified with mocked-API tests: OCO body carries `ordType=oco` + both legs, the 2nd-half
  SL stays single-leg `conditional`, legacy `conditional` OCOs still resolve, entry tracks
  the real fill, and `2 × sz_half` fits inside actual holdings.

## 2026-07-13 — PRODUCTION MODE ON + max 1 trade per scan

- **`TEST_MODE = False`** — testing finished and confirmed working. Production behavior
  now active: STRONG BUY needs score ≥ 5.0, the 30-min reversal gate is required,
  Claude Opus 4.8 decides and sizes every trade (10–30% of balance,
  performance-weighted cap), and the safety rails (BTC regime filter, max 3 open
  trades, 3-SL/24h circuit breaker) are enforced instead of logged-only.
  Fully reversible: setting `TEST_MODE = True` restores all test behavior unchanged.
- **`MAX_TRADES_PER_SCAN = 1`** (was 2 in production) — per user preference, only the
  single best-ranked STRONG BUY is traded per scan to keep things simple; lower-ranked
  signals wait for a later scan. The now-dead "refresh balance before the 2nd trade"
  block was removed; `backtest.py --per-scan` default updated 2 → 1 to match.

## 2026-07-08 — Daily Report simplified to a minimal heartbeat

Per user preference, the daily Telegram digest is now exactly two lines:
`💓 Daily Report — OKX Trading` + `📈 Open trades: N (COIN, COIN…)`. All performance
stats (win rate, profit factor, 7-day slice, best/worst coins, Fear & Greed, mode
line, dead-man hint) were removed from the message — that information lives in the
dashboard's 📊 Bot Performance panel instead. The once-per-UTC-day schedule, 08:00
gate, and dead-man-switch role are unchanged. Removed the now-unused
`_fetch_closed_trades()` helper (`fetch_fear_greed()` stays — the AI context uses it).

## 2026-07-08 — Test mode: fix the real bottleneck on test-trade cadence

User reported "still too slow" waiting for test trades. Root-caused with a live Supabase
query: it was **not** the STRONG_BUY_SCORE threshold — NEAR-USDT had been sitting open in
phase 1, and a rule blocked **every** new test trade while *any* test trade was active,
regardless of signal quality. Three changes were made, then two were rolled back at the
user's request as unnecessary once the actual fix proved sufficient on its own:

- **Kept:** replaced the "only ONE test trade at a time" block with `TEST_MAX_CONCURRENT = 3`
  (matches the production `MAX_OPEN_TRADES` cap) — a slow-moving trade no longer stalls
  every other signal. When slots are tight, the best-ranked candidates are kept (candidates
  are sorted by rank before slicing, not truncated arbitrarily). Verified via simulation:
  the exact "1 active trade + 2 new candidates" scenario that was silently dropping
  everything now correctly lets new trades through.
- **Reverted:** `STRONG_BUY_SCORE` (test mode) tried at 0.5, restored to **1.0**.
- **Reverted:** `MAX_TRADES_PER_SCAN` tried unified at 2, restored to **1 in test mode**
  (2 in production, unchanged).

## 2026-07-07 — Coin universe audit (33 → 38 coins)

Full audit against **live OKX spot data** (volume ranks, listing status, perp availability).
`SYMBOLS` (worker) and `DEFAULT_SCANNER` (browser) updated in sync; the browser now also
**drops removed coins** from saved localStorage lists (previously removals never synced).

- **Removed (6):**
  - RUNE, TON — **delisted from OKX spot** (confirmed via instruments API; the scanner had
    been burning 4 requests/scan each on dead pairs)
  - FLOKI — OKX volume collapsed to ~$0.1M/24h (rank #178): manipulation-prone candles
  - WIF — meme peak long past, OKX liquidity migrated away (~$0.4M, rank #111)
  - STRK — persistent unlock dilution, fading traction (~$0.8M)
  - ATOM — multi-year structural decline; the classic dip-buyer trap (~$0.4M, rank #124)
- **Added (11), all verified live on OKX with full candle history + perps:**
  - Majors: BNB, LTC, BCH, XLM (deep global liquidity, clean TA)
  - Blue-chip DeFi: UNI, AAVE
  - AI: TAO (Bittensor), WLD (Worldcoin)
  - High-momentum 2025-26 leaders: HYPE (Hyperliquid), MON (Monad), ZEC (Zcash revival)
- **Watch list (kept but monitor via profit-factor data):** TIA, INJ, POL, JUP, FET —
  legitimate projects with weak current OKX volume; prune if the digest shows chronic losses.
- **Considered and rejected:** TRUMP/PUMP/WLFI/PI (event-driven/manipulation-prone),
  OKB (exchange-token idiosyncrasy), XAUT/PAXG (gold, wrong asset class), SHIB (meme cohort
  already covered), ORDI/BLUR/ETC/ICP/PYTH (fading sectors), TRB (notorious manipulation),
  plus the new-listing churn at the top of the volume table (NES/RE/DATA/LIT/…).
- Docs updated (counts 33→38); manual AI advisor volatility-tier examples refreshed.
- Scan cost: ~152 OKX requests/scan (from ~132) — still comfortably inside the 60s cycle.

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
