# research/ — minute-level replay of the live rules

The evidence behind the 2026-09-16, 2026-09-21 and 2026-10-09 changes. Kept in the
repo because the first version lived in a temp folder and was wiped by Windows'
cleanup, taking the ability to re-check those numbers with it.

`backtest.py` (repo root) replays on hourly candles. This replays on **1-minute**
candles, which matters for exits: a stop and a target inside the same hour are
resolved in the order they actually happened, and the phase-1 break-even stop can
be modelled at all.

## Files

| File | What it does |
|---|---|
| `fetch_1m.py` | Downloads 1m (plus 30m/1H/4H warm-up) candles for every `SYMBOLS` coin into `research/<window>/`. Binance's public data mirror, because OKX is DNS-blocked on the development machine. Binance prices track OKX's closely, but its volumes are its own — the volume gate is a ratio to the same exchange's average, so it still works, but individual setups can differ from OKX's (SOL on 2026-09-20 qualified here and not live). MON is not on Binance; HYPE's history there is short. |
| `replay.py` | The engine. Real functions from `signal_checker.py` for every decision (signal, reversal gate, volume gate, ATR, support/resistance, exits, correlation guard, ranking); portfolio rules as in `run_scan`. The AI advisor is **not** modelled; $100 per trade. |
| `run_exp.py` | 17 exit / sizing / frequency variants over the four windows → `exp1.txt`. |
| `run_exp2.py` | Neighbourhood scans: break-even trigger (6 ATR-based, 6 fraction-of-TP) and score bar 3.5–5.0 → `exp2.txt`. |
| `run_exp3.py` | Where the break-even stop sits once triggered: entry, entry + fees, entry + fees + margin → `exp3.txt` (2026-10-19). |
| `run_exp4.py` | 2026-11-03 review: 19 untested ideas (share sold at TP, stop/TP multiples, post-stop pause, volume, deep-dip and crash filters) over five windows incl. the live period, plus a feature table → `exp4.txt`. |
| `run_exp5.py` | Stricter BTC regime filters → `exp5.txt`. |
| `live_review.py` | The live book by period, score, exit type, coin; AI exits vs the code's suggestion. Writes `live_rows.json` (git-ignored). |
| `live_vs_replay.py` | The live rules replayed over the live period, trade by trade against the real trades → `live_vs_replay.txt`. |
| `ai_exit_check.py` | Every live trade replayed on 1m prices with the AI's exits and with the code's → `ai_exit_check.txt`. |
| `skip_review.py` | Run **on the VPS** (needs the service key in `.env`): were the AI's SKIP decisions right? |
| `chk_bucket.py` | Every setup traded alone, by score bucket → `score_buckets.txt`. |
| `chk_score.py` | Score-4.0 trades vs 4.5+ trades inside the portfolio. |
| `live_stats.py` | The live record from Supabase (read-only, public key). |

## The strategy lab (2026-11-10)

`replay.py` answers "what would the live rule have done?". The lab answers a different
question — "does this *kind* of rule have an edge at all?" — which needs more market
regimes than 13 months of minute data hold. Hourly and daily bars, 2018/2022/2024 onward
depending on the test, numpy. Rules of the house are in `lab.py`'s docstring: signal on a
bar's close, fill at the next open, higher timeframes only from closed bars, 0.30% per
round trip, the stop wins a bar that touches both.

| File | What it asks | Answer |
|---|---|---|
| `funnel.py` | Gate by gate, why did the live rule not trade? → `funnel_2026-11-10.txt` | 168 setups qualified in 11 days; the BTC regime filter refused 165 |
| `market_map.py` | Monthly return of BTC and of the coin list; share of each month in the "bearish" regime → `market_map.txt` | — |
| `lab.py`, `lab_feat.py` | The hourly engine and its indicators (4H / 1D from closed bars only) | — |
| `lab_random.py` | What do random entries cost? → `lab_random.txt` | −0.37% per trade: the yardstick |
| `lab_exp1.py` | Does any hourly entry family have an edge (dip, trend pullback, breakouts, MACD turn; three exit sets)? | No. All negative; the live rule −0.58% (t −3.7) |
| `lab_d1.py`, `lab_exp2.py` | Multi-day breakout / trend / momentum rotation on the bot's coin list | Looks excellent |
| `fetch_all_1d.py`, `lab_exp3.py` | The same on a point-in-time universe (every Binance pair, top 50 by volume as of each day) | **Negative on every setting.** The result above was the coin list |
| `lab_exp4.py` | 14 simple daily trend rules on the 12 largest coins of Jan 2023, 2018–2026 | About the return of holding, half the drawdown; lost in 2025–26 |
| `fetch_derivs.py`, `lab_exp5.py` | Do open interest, funding, long/short ratio, taker flow mark the dips worth buying? | Only open interest: a fall with collapsing open interest rebounds |
| `lab_exp6.py` | Stress test of that: thresholds, holding time, stops, who and when, portfolio. Run again on 2022–2023 data never used to choose the rule → `lab_exp6_oos_2022-2023.txt` | Strong on 2024–26 (+3.7%/trade), weak on 2022–23 (+1.2%, not significant). Paper only |

```bash
python research/fetch_bars.py 1h 2024-01-01 2026-11-10      # ~3 min, 82 MB, git-ignored
python research/fetch_bars.py 1d 2023-01-01 2026-11-10
python research/fetch_all_1d.py 2023-01-01 2026-11-10
python research/fetch_derivs.py 2024-01-01 2026-11-09       # ~4 min, 31k small files -> 31 MB
python research/lab_exp1.py
python research/lab_exp6.py bars_1h derivs coin             # in-sample
python research/lab_exp6.py bars_1h_oos derivs_oos coin     # after fetching 2022-2023 into those folders
```

The lab needs `numpy` (research only; the worker itself still depends on `requests` alone).

**The lesson worth keeping:** `lab_exp2` vs `lab_exp3`. A coin list written today already
knows which coins survived and grew. Any "buy what is rising" rule tested on it is graded
on an exam it has seen. Test selection rules on a universe built from what was known at
the time, or do not believe them.

## Running it

```bash
python research/fetch_1m.py w_now "2026-07-10 00:00" "2026-10-08 00:00"   # ~6 min per window
python research/run_exp2.py
```

Window directories (`research/w*/`) are git-ignored — about 350 MB each. The
evaluation windows in `replay.WINDOWS` start 8 days after each download starts
(indicator warm-up) and do not overlap.

**The saved results were produced with the 4.5 bar and no break-even stop**, the
live rules on 2026-10-08. `replay.events_for()` defaults to the live
`STRONG_BUY_SCORE` and `portfolio()` adds a break-even stop only when a variant asks
for one, so re-running `run_exp.py` today gives a 4.0-bar baseline — different
numbers, not a contradiction.

## Reading results honestly

The rule this repo follows (see CHANGELOG 2026-09-03 onward): a change is adopted
only if its whole **neighbourhood** helps, not one lucky setting, and the result
holds across windows. Totals here are before the AI and before position sizing,
so they compare rules; they do not predict the live P&L. The replay was checked
against reality: it took 6 of the 12 live trades from 2026-09-21 to 2026-10-07
identically, with matching results (PENGU +7.2% live and replayed).
