"""Lab experiment 8: the bot can only buy. Would SELLING SHORT in downtrends have paid?

Since 30 Oct 2026 BTC has been below its trend line almost every hour, and a buy-only bot
correctly sits out. The obvious question: short the falling majors instead (OKX perpetual
swaps, 1x, no leverage). This is the mirror image of experiment 4's trend rules.

Same 12 coins that were the largest in January 2023 (no hindsight), daily bars 2018-2026,
decision on a daily close, executed at the next open, 0.30% round trip (spot cost; swap
fees are lower, so this is conservative). Each short holds a fixed number of coins until it
is closed, like a real swap position (no daily rebalancing).

Funding: a short RECEIVES funding when it is positive and PAYS when negative. In downtrends
it is often near zero or negative, so each rule is shown with three funding assumptions:
  0         no funding either way
  -4%/yr    the short pays 0.011%/day while open
  -11%/yr   the short pays 0.03%/day (harsh: about the size of bull-market funding, reversed)

Rules (short side): short while the daily close is below its N-day EMA; the 2% band version
enters 2% below and leaves 2% above; DON 20/10 shorts a break of the prior 20-day low and
covers above the prior 10-day high.

Read the split: 2018-2023 holds two bear markets (2018, 2022), 2024-26 is the recent period.
A rule that only made money in one of them is not one to trust with money.
"""
import calendar, os, sys, time
import numpy as np

import lab

HERE = os.path.dirname(os.path.abspath(__file__))
MAJORS = 'BTC ETH BNB XRP ADA DOGE SOL LTC TRX DOT AVAX LINK'.split()   # as lab_exp4: top by market cap, Jan 2023


def fetch(sym):
    """Daily bars saved by lab_exp4.py (run that first)."""
    return np.load(os.path.join(HERE, 'bars_maj_1d', f'{sym}.npy'))


def ema1(x, n):
    k, out = 2 / (n + 1), np.empty(len(x))
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = x[i] * k + out[i - 1] * (1 - k)
    return out

sys.stdout.reconfigure(encoding='utf-8')
D = 86_400_000
COST = lab.FEE + lab.SLIP


def short_state(name, c):
    """1 = want to be short after this close."""
    n = len(c)
    if name.startswith('EMA') and 'band' in name:
        N = int(name.split()[1]); e = ema1(c, N); st = np.zeros(n); on = False
        for i in range(n):
            if not on and c[i] < e[i] * 0.98: on = True
            elif on and c[i] > e[i] * 1.02: on = False
            st[i] = on
        return st
    if name.startswith('EMA'):
        return (c < ema1(c, int(name.split()[1]))).astype(float)
    if name.startswith('DON'):
        a, b = (int(x) for x in name.split()[1].split('/'))
        st = np.zeros(n); on = False
        for i in range(n):
            if i >= a:
                if not on and c[i] < c[i - a:i].min(): on = True
                elif on and c[i] > c[i - b:i].max(): on = False
            st[i] = on
        return st
    raise ValueError(name)


def run_short(a, name, fund_day, warm=200):
    """Daily equity of a 1x short that is open while the rule says so: a FIXED number of coins
    sold at the next open and bought back at the open after the rule flips (a real swap
    short, no daily rebalancing). Funding is charged per day held. A short that loses
    everything stays at zero (liquidated). Returns equity, trades."""
    o, c = a[:, 1], a[:, 4]
    st = short_state(name, c)
    st[:warm] = 0
    n = len(c)
    eq = np.ones(n); pos = False; trades = []; base = px = None; held = 0
    for i in range(1, n):
        want = st[i - 1]
        if want and not pos and eq[i - 1] > 0:     # sell short at today's open
            pos, base, px, held = True, eq[i - 1] * (1 - COST), o[i], 1
            eq[i] = base * (1 - (c[i] / px - 1)) * (1 - fund_day)
        elif not want and pos:                     # buy back at today's open
            eq[i] = base * (1 - (o[i] / px - 1)) * (1 - fund_day) ** held * (1 - COST)
            trades.append(eq[i] / (base / (1 - COST)) - 1); pos = False
        elif pos:
            held += 1
            eq[i] = base * (1 - (c[i] / px - 1)) * (1 - fund_day) ** held
        else:
            eq[i] = eq[i - 1]
        if eq[i] <= 0:                             # wiped out: liquidated, slice stays empty
            eq[i] = 0.0
            if pos:
                trades.append(-1.0); pos = False
    return eq, trades

data = {m: fetch(m + 'USDT') for m in MAJORS}
last = min(a[-1, 0] for a in data.values())
print(f"12 majors, daily, data to {time.strftime('%Y-%m-%d', time.gmtime(last / 1000))}; short 1x, 0.30% round trip\n")

RULES = ['EMA 20', 'EMA 50', 'EMA 50 band', 'EMA 100', 'DON 20/10']
FUNDING = [('0', 0.0), ('-4%/yr', 0.00011), ('-11%/yr', 0.0003)]

# portfolio: 12 equal slices from 2019 (needs 200 warm-up days; coins join when they have them)
t_start = calendar.timegm(time.strptime('2019-01-01', '%Y-%m-%d')) * 1000
days = np.arange(t_start, last + 1, D)
year = np.array([time.gmtime(t / 1000).tm_year for t in days])


def port_rets(rname, fund):
    rets = np.zeros(len(days)); w = np.zeros(len(days))
    for m, a in data.items():
        eq, _ = run_short(a, rname, fund)
        t = a[:, 0]
        r = np.zeros(len(days)); live = np.zeros(len(days))
        for j, d in enumerate(days):
            i = int((d - t[0]) // D)
            if 200 < i < len(eq):
                r[j] = eq[i] / eq[i - 1] - 1 if eq[i - 1] > 0 else 0; live[j] = 1
        rets += r; w += live
    return np.where(w > 0, rets / np.maximum(w, 1), 0.0)


def line(rets, mask):
    r = rets[mask]
    eq = np.cumprod(1 + r)
    dd = float(np.max(1 - eq / np.maximum.accumulate(eq)))
    cagr = eq[-1] ** (365 / len(r)) - 1
    sh = r.mean() / r.std() * np.sqrt(365) if r.std() > 0 else 0
    return cagr, dd, sh


print('12-coin SHORT portfolio (equal slices, a slice is in cash while its coin is not shorted):')
print(f"{'rule':<13}{'funding':>9}   {'2019-2023':>26}   {'2024-2026':>26}   by year")
for rname in RULES:
    for fname, f in FUNDING:
        rets = port_rets(rname, f)
        a_ = line(rets, year <= 2023); b_ = line(rets, year >= 2024)
        yr = ' '.join(f"{y % 100}:{np.prod(1 + rets[year == y]) - 1:+.0%}" for y in sorted(set(year)))
        print(f"{rname:<13}{fname:>9}   cagr {a_[0]:>+5.0%} dd {a_[1]:>4.0%} sh {a_[2]:>5.2f}   "
              f"cagr {b_[0]:>+5.0%} dd {b_[1]:>4.0%} sh {b_[2]:>5.2f}   {yr}")
    print()

# the combined book: long above the line (experiment 4), short below it
print('BTC alone, EMA 50 band, funding -4%/yr: trades per year, win rate, average')
eq, tr = run_short(data['BTC'], 'EMA 50 band', 0.00011)
tr = np.array(tr); yrs = (len(eq) - 200) / 365
print(f"  {len(tr) / yrs:.1f} shorts/yr  win {np.mean(tr > 0):.0%}  avg {tr.mean():+.1%}  "
      f"best {tr.max():+.0%}  worst {tr.min():+.0%}")
