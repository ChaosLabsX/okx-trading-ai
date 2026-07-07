# Backtesting Harness (`backtest.py`)

A "time machine" for the bot's rules: replays the **production** signal + Option 3 exit
logic over historical OKX candles and prints a report card — so parameters can be tuned
with evidence instead of live losses. It imports the real functions from
`signal_checker.py` (signal engine, ATR/support-resistance exits, regime filter,
reversal gate, candidate ranking), so what is tested is exactly what trades.

Runs locally, uses only OKX's free public history endpoint, places no orders,
touches no keys.

## Usage

```bash
python backtest.py                                   # 90 days, all 33 coins, production settings
python backtest.py --days 60 --score 4.5             # test a looser STRONG BUY bar
python backtest.py --no-regime                       # measure the BTC filter's effect
python backtest.py --no-reversal                     # measure the reversal gate's effect
python backtest.py --atr-sl 3.0                      # test wider stops
python backtest.py --coins BTC-USDT,SOL-USDT --days 30
python backtest.py --refresh                         # re-download candles (else disk-cached)
```

First run downloads candles (~3–5 min for all 33 coins) into `backtest_cache/`
(git-ignored); subsequent runs are instant, so A/B comparisons are cheap.

| Flag | Default | Meaning |
|---|---|---|
| `--days` | 90 | History window |
| `--score` | 5.0 | STRONG BUY threshold (production value) |
| `--atr-tp / --atr-sl / --atr-trail` | 2.0 / 2.5 / 1.0 | ATR exit multipliers |
| `--stake` | 100 | USD per simulated trade (no compounding) |
| `--max-open / --per-scan` | 3 / 2 | Concurrency caps (production values) |
| `--no-regime / --no-reversal` | off | Disable a gate to measure its effect |

## Reading the report

- **Profit factor** (money won ÷ money lost) is the headline number: > 1.5 good, < 1.0 losing.
- **Gates line** shows where signals died (regime-blocked, no-reversal, slot-capped) —
  useful for understanding *why* there were few trades.
- **Per-coin table** is the pruning shopping list.

## Simulation model (honest fine print)

- Signal at candle close → entry at the **next candle's open** (no look-ahead).
- Exits per candle: SL assumed **first** when TP and SL fall in the same candle
  (conservative); gap-throughs fill at the open (worse for SL, better for TP);
  after the partial TP, the trailing stop tracks candle highs and exits on a
  `trail%` pullback (starting the following candle).
- 0.1% taker fee on every fill (ignores the live maker-entry saving — conservative).
- Reversal confirmation approximated with 1H candles (production uses 30m).
- The AI layer is **not** simulated — exits use the deterministic ATR+structure
  baseline the AI starts from; no funding/news/order-book history exists.
- Per-coin 4h re-entry cooldown approximates the production zone dedup.

## First real findings (2026-07-07, 6 majors × 45 days, production settings)

- With everything on: **124 STRONG BUY signals — all blocked by the BTC regime filter**
  (the period was a BTC downtrend).
- With `--no-regime`: those signals became 7 trades → 43% win rate, profit factor
  **0.62**, net **−$4.35** per $100 stakes. The reversal gate alone blocked 104 of 112.
- Conclusion: during this bear stretch the regime filter did its job — the trades it
  suppressed would have lost money. Re-test across other periods before touching it.

## The overfitting warning

A backtest is a report card on the past, not a promise. Change **few** knobs, prefer
settings that win across **different** periods (`--days 30/60/90`), and treat small
differences as noise. If a setting isn't clearly better, it isn't better.
