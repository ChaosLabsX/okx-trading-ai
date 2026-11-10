"""Lab experiment 4: the one thing that survived experiment 3 was 'hold BTC only while it is
above its 50-day average'. Is that one lucky line, or a robust effect?

Test: simple daily trend rules on the coins that were ALREADY the largest in January 2023
(so nothing here is picked with hindsight), each coin on its own, over 2018-2026 where the
coin existed: two bull markets, two bear markets, and the choppy stretch since 2025.

  EMA N        long while the daily close is above its N-day EMA
  EMA N band   same, but enter 2% above / leave 2% below (fewer whipsaws)
  X 20/50      long while EMA20 > EMA50
  DON 20/10    in above the prior 20-day high, out under the prior 10-day low
  MOM 30       long while the 30-day return is positive

Each decision is made on a daily close and executed at the next open; 0.30% round trip.
Output per rule: for each coin CAGR / max drawdown, then the 12-coin equal-slice portfolio.
"""
import calendar, os, sys, time
import numpy as np
import requests

import lab

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'bars_maj_1d')
os.makedirs(OUT, exist_ok=True)
D = 86_400_000
MAJORS = 'BTC ETH BNB XRP ADA DOGE SOL LTC TRX DOT AVAX LINK'.split()   # top by market cap, Jan 2023
FEE, SLIP = lab.FEE, lab.SLIP
COST = FEE + SLIP


def fetch(sym):
    fp = os.path.join(OUT, f'{sym}.npy')
    if os.path.exists(fp):
        return np.load(fp)
    rows, t = [], calendar.timegm(time.strptime('2018-01-01', '%Y-%m-%d')) * 1000
    end = int(time.time() * 1000)
    while t < end:
        r = requests.get('https://data-api.binance.vision/api/v3/klines',
                         params={'symbol': sym, 'interval': '1d', 'startTime': t, 'limit': 1000}, timeout=30)
        b = r.json()
        if not b:
            break
        rows.extend(b); t = b[-1][0] + D
    rows = [r for r in rows if r[0] + D <= end]
    a = np.array([[r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])] for r in rows])
    np.save(fp, a)
    return a


def ema1(x, n):
    k, out = 2 / (n + 1), np.empty(len(x))
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = x[i] * k + out[i - 1] * (1 - k)
    return out


def rule_state(name, c):
    """1 = want to be long after this close."""
    n = len(c)
    if name.startswith('EMA') and 'band' in name:
        N = int(name.split()[1]); e = ema1(c, N); st = np.zeros(n); on = False
        for i in range(n):
            if not on and c[i] > e[i] * 1.02: on = True
            elif on and c[i] < e[i] * 0.98: on = False
            st[i] = on
        return st
    if name.startswith('EMA'):
        return (c > ema1(c, int(name.split()[1]))).astype(float)
    if name.startswith('X '):
        a, b = (int(x) for x in name.split()[1].split('/'))
        return (ema1(c, a) > ema1(c, b)).astype(float)
    if name.startswith('DON'):
        a, b = (int(x) for x in name.split()[1].split('/'))
        st = np.zeros(n); on = False
        for i in range(n):
            if i >= a:
                if not on and c[i] > c[i - a:i].max(): on = True
                elif on and c[i] < c[i - b:i].min(): on = False
            st[i] = on
        return st
    if name.startswith('MOM'):
        N = int(name.split()[1]); st = np.zeros(n)
        st[N:] = c[N:] > c[:-N]
        return st
    raise ValueError(name)


def run(a, name, warm=200):
    o, c = a[:, 1], a[:, 4]
    st = rule_state(name, c)
    st[:warm] = 0
    n = len(c)
    eq = np.ones(n); pos = 0.0; trades = []; entry = None
    for i in range(1, n):
        want = st[i - 1]
        r_day = c[i] / c[i - 1] - 1
        if want and not pos:                      # buy at today's open
            eq[i] = eq[i - 1] * (c[i] / o[i]) * (1 - COST); pos = 1.0; entry = eq[i - 1]
        elif not want and pos:                    # sell at today's open
            eq[i] = eq[i - 1] * (o[i] / c[i - 1]) * (1 - COST); pos = 0.0
            trades.append(eq[i] / entry - 1)
        else:
            eq[i] = eq[i - 1] * (1 + r_day * pos)
    return eq, trades, st


def perf(eq, t, warm=200):
    e = eq[warm:]
    yrs = len(e) / 365.0
    cagr = (e[-1] / e[0]) ** (1 / yrs) - 1 if e[0] > 0 else 0
    dd = float(np.max(1 - e / np.maximum.accumulate(e)))
    dr = np.diff(e) / e[:-1]
    sh = dr.mean() / dr.std() * np.sqrt(365) if dr.std() > 0 else 0
    return cagr, dd, sh


data = {m: fetch(m + 'USDT') for m in MAJORS}
RULES = ['EMA 20', 'EMA 30', 'EMA 50', 'EMA 100', 'EMA 200', 'EMA 50 band', 'EMA 100 band',
         'X 20/50', 'X 10/30', 'DON 20/10', 'DON 55/20', 'MOM 30', 'MOM 60', 'MOM 90']

print('first bar per coin: ' + ', '.join(f"{m} {time.strftime('%Y-%m', time.gmtime(a[0, 0] / 1000))}" for m, a in data.items()))
print('\nCAGR / max drawdown per coin (each from its own first tradable day):')
print(f"{'rule':<14}" + ''.join(f'{m:>12}' for m in MAJORS) + '   beats hold on DD   CAGR>0')
hold = {}
for m, a in data.items():
    c = a[:, 4]
    hold[m] = perf(c / c[0], a[:, 0])
print(f"{'buy & hold':<14}" + ''.join(f"{hold[m][0]:>+6.0%}/{hold[m][1]:>4.0%} " for m in MAJORS))
res = {}
for rname in RULES:
    row, better, pos = [], 0, 0
    for m, a in data.items():
        eq, tr, st = run(a, rname)
        p = perf(eq, a[:, 0]); res[(rname, m)] = (eq, tr, st, a[:, 0])
        row.append(f"{p[0]:>+6.0%}/{p[1]:>4.0%} ")
        better += p[1] < hold[m][1]; pos += p[0] > 0
    print(f'{rname:<14}' + ''.join(row) + f'      {better:>2}/12         {pos:>2}/12')

# portfolio: 12 equal slices, each in its coin when the rule says so, else cash. Common calendar from 2021.
print('\n12-coin portfolio (equal slices, a slice is in cash while its coin is out), by year:')
t_start = calendar.timegm(time.strptime('2021-01-01', '%Y-%m-%d')) * 1000
t_end = min(a[-1, 0] for a in data.values())
days = np.arange(t_start, t_end + 1, D)
year = np.array([time.gmtime(t / 1000).tm_year for t in days])


def port(rname):
    rets = np.zeros(len(days))
    for m in MAJORS:
        eq, tr, st, t = res[(rname, m)] if rname != 'hold' else (data[m][:, 4] / data[m][0, 4], [], None, data[m][:, 0])
        i0 = int((t_start - t[0]) // D)
        e = eq[i0 - 1:i0 + len(days)]
        rets += (e[1:] / e[:-1] - 1)[:len(days)] / len(MAJORS)
    return np.cumprod(1 + rets), rets


for rname in ['hold'] + RULES:
    eq, rets = port(rname)
    dd = float(np.max(1 - eq / np.maximum.accumulate(eq)))
    sh = rets.mean() / rets.std() * np.sqrt(365)
    yr = ' '.join(f"{y % 100}:{np.prod(1 + rets[year == y]) - 1:+.0%}" for y in sorted(set(year)))
    cagr = eq[-1] ** (365 / len(days)) - 1
    print(f"{rname:<14} cagr {cagr:>+5.0%}  dd {dd:>4.0%}  sharpe {sh:>5.2f} | {yr}")

print('\ntrades per year and win rate, BTC:')
for rname in RULES:
    eq, tr, st, t = res[(rname, 'BTC')]
    yrs = (len(eq) - 200) / 365
    tr = np.array(tr)
    print(f"{rname:<14} {len(tr) / yrs:>4.1f} trades/yr  win {np.mean(tr > 0):>4.0%}  avg {tr.mean():>+6.1%}  "
          f"in market {st[200:].mean():>4.0%}")
