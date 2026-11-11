# Strategies — what runs, on what evidence, and what it takes to trade real money

Written 2026-11-10, after an audit that asked one question of every rule in this bot:
**does it make money after fees, measured on data that was not used to design it?**

The short answer: the original strategy does not, nothing at its timeframe does, and two
candidates that might are now being tested. This file is the record of that, and the
rulebook for what happens next.

## 1. The ladder (since 2026-11-11)

Every strategy stands on one rung, recomputed from its own record every few minutes
(`strategies.py`):

| Rung | What it does |
|---|---|
| `paper` | Same signals, gates, AI call, exits, fees and Telegram messages (marked PAPER) — but **no order is sent**. Fills are simulated minute by minute against real OKX candles (`paper_trades`). |
| `trial` | **Real money, $20 a trade.** |
| `full` | Real money at full size: 10% of the account a trade (crash rebound), 8% (trend), the AI's own sizing under the risk limits (dip). |

How a strategy moves, with nobody touching anything:

| Move | When |
|---|---|
| paper → trial | its record passes the bar (section 4) |
| trial → full | it still passes, with at least 10 real trades in that record |
| any → paper | it has lost money on average over 30+ trades, **or** its real trades lost more than 5% of the account in 30 days |

The record is every closed trade of that strategy **since 2026-11-10**, real and paper
together (same rule, same exits, same costs). Every move is announced on Telegram, and the
first run after an update sends the whole line-up.

**Starting rungs (owner's choice, 2026-11-11):** crash rebound starts on a **trial** —
real money now. Dip and trend start on paper and must earn their way up.

`STRATEGY_MODE` in `signal_checker.py` can override any strategy by hand: `'auto'` (the
ladder, the default), `'paper'`, `'off'`, or `'live'` (full size, no automatic demotion —
the owner's responsibility). `STRATEGY_START` sets where an `'auto'` strategy starts.

Real money lives in `option3_trades` and nowhere else; `paper.py` never calls an OKX
private endpoint and `tests/test_paper.py` fails if it ever does. Crash-rebound and trend
positions share that table with the dip strategy's (`strategy` column), so the dashboard's
live record and the monthly loss limit see every real trade.

### How a real crash-rebound or trend trade works

1. Market buy for the stake (in USDT). The coins received are read back from OKX (the fee
   is taken in coins).
2. **A protective stop on OKX for every coin, 30% below entry**, live 24/7 whether or not
   the bot is running. If OKX refuses it, the coins are sold straight back. If the trade
   cannot be saved to the database, it is undone too — nobody would ever sell it.
3. The bot exits by itself: crash rebound after 48 hours, trend when the daily rule says
   so. It marks the row before cancelling the stop, so a crash between cancel and sale is
   finished on the next pass. If the sale is refused, the stop goes straight back on.
4. The result is exact: what the sale brought in after its fee, minus what the buy cost.

Why −30%: it must exist (a hack or a delisting can take a coin down 60% in an hour), but a
close stop ruins this trade — after a crash the price whips around before it recovers. In
testing, −15% cut the average from +3.7% to +1.4% a trade; −30% fired on 3 of 205 trades
(2024–26) and 7 of 201 (2022–23) and cost almost nothing (`research/lab_exp7.txt`). The risk
is held by size: at $20, the worst the stop allows is about $6.

Tested against a simulated OKX that refuses what the real one refuses
(`tests/test_live_simple.py`, 27 checks): every failure above, the owner's own coins never
sold, and the recorded P&L equal to the account's real balance change.

## 2. The three strategies and their evidence

All figures are **after costs** (0.10% fee per side, plus 0.05% slippage per side in the
lab tests: 0.30% per round trip).

### `dip` — the original (STRONG BUY + 30m reversal + 2× volume + BTC regime + AI) → PAPER since 2026-11-10

| Measurement | Result |
|---|---|
| Live record, 46 closed trades, Jul–Oct 2026 | **−0.56% per trade** (22 won; net −$43.73) |
| Minute replay of the live rule, 13 months (`research/replay.py`) | −0.07% to −0.35% per trade, depending on the setting |
| Hourly test, 34 months, 77 coins (`research/lab_exp1.py`) | **−0.58% per trade, t = −3.7**; negative in 2024, 2025 and 2026 |

No measurement shows an edge. Buying at random moments with the same exits costs −0.37%
per trade (`research/lab_random.txt`): the rule did no better than that, and if anything
slightly worse. The trade journal had been saying why in its own words: 13 of 23
stop-losses were followed by a bounce within 24 hours — the fall continues, stops the
trade out, then turns.

It also explains the droughts. The rule buys heavy-volume sell-offs; the BTC regime filter
refuses to buy while the market is falling; so signals and permission rarely coincide.
Between 30 Oct and 10 Nov 2026, 168 setups qualified and the filter refused 165
(`research/funnel_2026-11-10.txt`). The filter is doing its job — loosening it lost money
in every window tested. The rule underneath it is what does not pay.

**Why paper and not just smaller:** a smaller size loses the same percentage more slowly.
On paper the strategy keeps running exactly as before — including the AI's decisions —
so if it ever does show an edge, there will finally be evidence of it.

### `flush` — crash rebound (liquidation flush) → REAL MONEY, $20 trial, since 2026-11-11

**Rule:** a coin is down 8%+ over 24h **and** its futures open interest, counted in coins,
fell 15%+ over the same 24h → buy; sell 48 hours later, whatever the price. No target;
a protective stop 30% below entry only for disasters. One trade per coin per 72h. At most
4 real positions at once; signals beyond that are paper-traded, so none is wasted as
evidence. Fires about 6–9 times a month, on 2–5 different days (`lab_exp7.txt`).

**Why it might work:** a fall that comes with collapsing open interest is forced selling —
leveraged longs being liquidated — and forced selling ends. A fall where open interest
holds or grows is sellers adding, which need not end.

**Evidence** (30 liquid coins, hourly, `research/lab_exp5.py`, `lab_exp6.py`):

| Fall ≥ 8% in 24h, and open interest (coins) down at least… | 2024–2026 (used to choose the rule) | 2022–2023 (never looked at until the rule was fixed) |
|---|---|---|
| anything | +0.27% | +0.92% |
| 10% | +2.85% | +0.21% |
| **15%** | **+3.70%** (205 trades, every year positive) | **+1.15%** (201 trades; 2022 −0.48%, 2023 +2.44%; not significant) |
| 20% | +5.52% | +0.89% |

On the data used to find it the effect is large and grows steadily with the size of the
collapse — the shape a real effect has. On the earlier, untouched data it is positive but
small and statistically indistinguishable from zero, and the steady growth is gone. So:
**promising, unproven.** The owner chose to test it with real money at $20 a trade rather
than wait for paper results; the ladder takes it back to paper by itself if it loses.

**What weakens it:** a stop, and a target. After a flush the price whips around before it
recovers. On the hardest flushes (open interest down 20%+, 2024–26) the average was +5.5%
with no stop, +2.9% with a −20% stop, +1.7% with a −10% stop and zero with a −5% stop;
selling everything at +6% removed the edge altogether, because a few large rebounds pay
for the rest. Risk therefore has to be carried by **small size**, not by a stop: the
worst single trade at the 15% setting was −20% (2024–26) and −33% (2022–23).

**Live data source:** OKX's public statistics endpoint (`rubik/stat/contracts/open-interest-volume`),
dollars converted to coins by dividing out the price move. The research used Binance's
archive, so paper trading is also the test of whether OKX's figure behaves the same.

### `trend` — daily trend on the 12 largest coins → PAPER, until it earns a trial

**Rule:** for each of BTC, ETH, BNB, XRP, ADA, DOGE, SOL, LTC, TRX, DOT, AVAX, LINK (the
largest coins of January 2023, so the list is not picked with hindsight): buy when the
daily close is 2% above its 50-day EMA, sell when it closes 2% below. Checked once a day.

**Evidence** (`research/lab_exp4.py`, 2018–2026): 14 simple trend rules × 12 coins.
Against buying and holding, a trend rule had a smaller worst drawdown on 7–12 of the 12
coins and a positive return on 9–11. As a 12-coin portfolio since 2021: about the return
of holding (+34% to +50% a year against +46%) with a worst drawdown of 40–65% instead of
79% (the rule used here, EMA 50 with a 2% band: +44% a year, worst drawdown 52%). **It lost in 2025 (−5% to −31%) and 2026 (−18% to +1%)**: in a sideways or falling
market a trend rule gets whipsawed. It is the one rule here with eight years behind it,
and it is slow — a handful of trades per coin per year.

## 3. Tested and rejected — do not re-test without a new idea

| Idea | Result | Where |
|---|---|---|
| Other hourly entries: trend pullback, 24/48/96h breakouts, 20-day breakout, MACD turn | All negative, −0.2% to −0.6% per trade; random entries cost −0.37% | `lab_exp1.txt`, `lab_random.txt` |
| Wider exits on the same entries (TP 3–4× ATR, trail 2–3× ATR) | Still negative | `lab_exp1.txt` |
| Buying the 3–5 strongest coins of the last 3–28 days (momentum rotation) | Looked excellent (+60% a year) on today's coin list; **negative on every setting** once the coins were chosen without hindsight (the top 50 by volume as of each day, delisted coins included) | `lab_exp2.txt` vs `lab_exp3.txt` |
| Daily breakouts on alts | Same: an artefact of the coin list | `lab_exp3.txt` |
| Funding-rate extremes, long/short ratio, taker flow as entry signals | No significant effect | `lab_exp5.txt` |
| 24 variations of the dip rule's stops, targets, filters | Not robust (2026-11-03) | `exp4.txt`, `exp5.txt` |

The momentum result is the one to remember. A coin list written in 2026 already knows
which coins survived and grew; any "buy what is going up" test on that list is graded on
an exam it has seen. The equal-weight basket of that list lost 30% over the test period;
the honest basket lost 85%.

## 4. The bar for real money

Set on 2026-11-10, before a single paper trade existed, so it cannot drift toward
whatever the results turn out to be. Since 2026-11-11 the worker applies it by itself
(`strategies.decide`); the dashboard's Strategies tab shows each check.

A strategy **passes** when all of these hold over its record (real and paper trades since
2026-11-10):

1. **30 or more closed trades.**
2. **Average result per trade above zero**, after fees and slippage.
3. **Profit factor 1.3 or better** (gains ÷ losses).
4. **Not luck**: trades opened on the same day are first averaged into one result (a
   market-wide crash moves every coin together, so twenty trades from one day are one
   result, not twenty); the average of those daily results must clear 1.3 standard
   errors (roughly 9-in-10 confidence the true average is not zero or worse).

Passing moves a paper strategy to a **$20 trial**, and a trial strategy with 10+ real
trades in its record to **full size**. It is permission to keep going, not proof.

**Demotion, also automatic:** back to paper when the record has lost money on average
over 30+ trades, or when the strategy's real trades lost more than 5% of the account in
the last 30 days. The account-wide monthly loss limit (section 5) stops every strategy's
new real trades on top of that.

One check stays human: **do the results look like the backtest** — same order of size,
earned the same way, not one outlier carrying the rest? The Strategies tab and the daily
Telegram report are there so someone looks.

## 5. Risk limits for live trades

| Limit | Value | What it does |
|---|---|---|
| `RISK_PER_TRADE_PCT` | 1.0 | A stop-out may cost at most 1% of the whole account, so the stop distance sets the size: ~26% of the account at a 3.8% stop, ~14% at 7%. Applied on top of the 45–60% ladder; it can only shrink a position. |
| `MAX_30D_LOSS_PCT` | 6.0 | When closed live trades of the last 30 days have lost more than 6% of the account, new live trades stop until the losses age out. Open trades are still managed. |
| `MAX_OPEN_TRADES` | 3 | Unchanged. |
| `MAX_SL_PER_DAY` | 3 | Unchanged (circuit breaker, dip only). |
| `TRIAL_STAKE_USDT` | 20 | Size of every trade on a trial rung. |
| `LOSS_BUDGET_PCT` | 5.0 | A strategy whose real trades lost more than this share of the account in 30 days goes back to paper. |
| `DISASTER_STOP_PCT` | 30 | The protective stop under every crash-rebound and trend position, on OKX. |
| `LIVE_MAX_OPEN` | flush 4, trend 12 | Real positions per strategy at once. Never two real strategies in one coin. |

Why the first one exists: July ran at about $12 a trade, September and October at about
$130. The same −1.4% to −2.2% per trade that cost $2 in July cost $27 a month later.

## 6. How a paper trade is simulated

- Entry at the current OKX price plus 0.05% slippage; both fees charged as
  `_exit_pnl` counts them; exits pay slippage too.
- Stepped on finished **1-minute** candles (coarser ones only after an outage). Inside
  one candle the stop is assumed to be hit before the target.
- `dip` uses the full Option 3 shape — stop, half sold at the target, break-even move at
  70% of the way, trailing stop on the rest. `tests/test_paper.py` proves on 400 random
  trades that this matches `research/replay.py`, the engine every exit decision in this
  repo was measured with, to the last decimal.
- Worst and best point reached while open are recorded (`mae_pct`, `mfe_pct`), so the
  question "what would a stop have cost?" can be answered from the trades themselves.

## 7. Every "no" is on the record

A qualified signal that is not traded is written to `skipped_setups` with the gate that
refused it — the AI, the BTC regime filter, the open-trade cap, the circuit breaker, the
monthly loss limit, the correlation guard, crowded funding, or ranking below the top
pick. Twenty-four hours later the bot checks what the price did and marks each refusal
*would have lost*, *would have won* or *neither*. The dashboard's **Why no trade?** tab
shows the tally per gate. A filter that keeps refusing winners will show up there, and so
will one that is earning its keep.

## 8. Re-running the evidence

```bash
python research/fetch_bars.py 1h 2024-01-01 2026-11-10      # hourly history, 77 coins
python research/fetch_bars.py 1d 2023-01-01 2026-11-10
python research/fetch_all_1d.py 2023-01-01 2026-11-10       # every Binance pair, for the hindsight test
python research/fetch_derivs.py 2024-01-01 2026-11-09       # open interest and funding
python research/lab_exp1.py                                 # ... lab_exp6.py
```

See `research/README.md` for what each experiment asks.
