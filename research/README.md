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
| `chk_bucket.py` | Every setup traded alone, by score bucket → `score_buckets.txt`. |
| `chk_score.py` | Score-4.0 trades vs 4.5+ trades inside the portfolio. |
| `live_stats.py` | The live record from Supabase (read-only, public key). |

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
