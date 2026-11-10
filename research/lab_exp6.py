"""Lab experiment 6: stress-test the one promising result of experiment 5 - buying a
"liquidation flush" (a sharp 24h fall during which futures open interest collapses).

Experiment 5 showed the rebound is real on average but the live exits (a tight stop) give
it all back: after a flush the price whips around before it recovers. So this tests the
shape that suits a rebound: buy, hold a fixed number of hours, with only a far "disaster"
stop. Questions, in order:

  1. Is it one lucky threshold?         -> grid over fall size x open-interest drop
  2. Is it a few lucky days?            -> by year, median, share of winners, worst trades
  3. Market-wide flushes or single-coin ones?   (single-coin = possibly bad news)
  4. How long to hold, and where does the disaster stop cost more than it saves?
  5. As a portfolio with small fixed slices: return and drawdown, by year.

One event per coin per 72h. Returns are net of 0.30% round-trip costs unless marked gross.
"""
import os, sys
import numpy as np
import lab
from lab_feat import features

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
H = lab.H
BARS = sys.argv[1] if len(sys.argv) > 1 else 'bars_1h'
DER = sys.argv[2] if len(sys.argv) > 2 else 'derivs'
OI_MODE = sys.argv[3] if len(sys.argv) > 3 else 'coin'      # 'coin' = contracts; 'usd' = notional (falls with price)
if len(sys.argv) > 4:
    lab.SLIP = float(sys.argv[4])
FEE, SLIP = lab.FEE, lab.SLIP
have = {f[:-4] for f in os.listdir(os.path.join(HERE, BARS))}
syms = sorted(f[:-4] for f in os.listdir(os.path.join(HERE, DER))
              if f.endswith('.npy') and '.fund' not in f and f[:-4] + 'USDT' in have)
mk = lab.Market(BARS, only={s + 'USDT' for s in syms})
with np.errstate(all='ignore'):
    F = features(mk)
C, n = mk.c.shape
c, o, hi, lo = mk.c, mk.o, mk.h, mk.l
oi = np.full((C, n), np.nan)
for s in syms:
    k = mk.ix[s + 'USDT']
    a = np.load(os.path.join(HERE, DER, f'{s}.npy'))
    idx = ((a[:, 0] - mk.t0) // H).astype(int)
    okk = (idx >= 0) & (idx < n)
    oi[k, idx[okk]] = a[okk, 1]
    last = np.nan
    for i in range(n):
        if np.isnan(oi[k, i]): oi[k, i] = last
        else: last = oi[k, i]
if OI_MODE == 'coin':
    oi = oi / c                                            # notional -> coins: strip out the price move itself
with np.errstate(all='ignore'):
    chg = lambda x, N: np.where(np.arange(n)[None, :] >= N, x / np.roll(x, N, axis=1) - 1, np.nan)
    ret24, oi24 = chg(c, 24), chg(oi, 24)
b = mk.ix['BTCUSDT']
btc24 = ret24[b]
year = np.array([__import__('time').gmtime(t / 1000).tm_year for t in mk.t])
i0 = 24 * 45
MAJ = {mk.ix[s + 'USDT'] for s in 'BTC ETH BNB SOL XRP ADA DOGE LTC LINK AVAX'.split() if s + 'USDT' in mk.ix}


def events(p, q, cooldown=72, mask=None):
    ev = (ret24 <= -p) & (oi24 <= -q)
    ev[:, :i0] = False
    if mask is not None:
        ev &= mask
    out = []
    for k in range(C):
        last = -10**9
        for i in np.flatnonzero(ev[k]):
            if i - last >= cooldown and i + 2 < n:
                out.append((k, i)); last = i
    return out


def hold(k, i, hours, stop=None, tp=None):
    """Buy at the open of bar i+1, sell at the close `hours` later; optional disaster stop / target (%)."""
    ie = i + 1
    e = o[k, ie] * (1 + SLIP)
    net = lambda px: (px * (1 - SLIP) - e) / e - FEE * (1 + px * (1 - SLIP) / e)
    end = min(ie + hours, n) - 1
    for j in range(ie, end + 1):
        if stop and lo[k, j] <= e * (1 - stop / 100):
            px = e * (1 - stop / 100)
            return j, 'stop', net(min(o[k, j], px) if j > ie else px)
        if tp and hi[k, j] >= e * (1 + tp / 100):
            px = e * (1 + tp / 100)
            return j, 'tp', net(max(o[k, j], px) if j > ie else px)
    return end, 'time', net(c[k, end])


def summarize(tr):
    r = np.array([t[2] for t in tr]) * 100
    if len(r) < 5:
        return None
    dm = {}
    for (k, i), x in zip([t[3] for t in tr], r):
        dm.setdefault(int(mk.t[i] // 86_400_000), []).append(x)
    d = np.array([np.mean(v) for v in dm.values()])
    t = d.mean() / (d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 2 else float('nan')
    win, loss = r[r > 0].sum(), -r[r <= 0].sum()
    yr = {}
    for (k, i), x in zip([t[3] for t in tr], r):
        yr.setdefault(int(year[i]), []).append(x)
    return dict(n=len(r), days=len(d), avg=r.mean(), med=np.median(r), win=(r > 0).mean(), t=t,
                pf=win / loss if loss else 9.99, worst=r.min(), p5=np.percentile(r, 5),
                yr=' '.join(f"{y % 100}:{len(v):>3}/{np.mean(v):+5.2f}" for y, v in sorted(yr.items())))


def show(name, tr):
    s = summarize(tr)
    if not s:
        print(f'{name:<44} too few'); return
    print(f"{name:<44} n {s['n']:>4} days {s['days']:>3}  avg {s['avg']:+5.2f}%  med {s['med']:+5.2f}%  win {s['win']:>4.0%}  "
          f"t {s['t']:+4.1f}  PF {s['pf']:4.2f}  worst {s['worst']:+6.1f}%  5%ile {s['p5']:+6.1f}% | {s['yr']}")


run = lambda evs, hours, stop=None, tp=None: [(*hold(k, i, hours, stop, tp), (k, i)) for k, i in evs]

print(f'{C} coins, {lab.fmt(mk.t[i0])} -> {lab.fmt(mk.t[-1])}\n')
print('1. threshold grid - hold 48h, no stop (net of costs)')
for p in (0.05, 0.08, 0.12):
    for q in (0.0, 0.05, 0.10, 0.15, 0.20, 0.25):
        show(f'fall >= {p:.0%}, open interest down >= {q:.0%}', run(events(p, q), 48))
    print()

E = events(0.08, 0.20)
print('2/4. fall >= 8%, OI down >= 20%: holding period and disaster stop')
for hours in (12, 24, 48, 72, 120):
    show(f'hold {hours}h, no stop', run(E, hours))
for stop in (20, 15, 10, 7, 5):
    show(f'hold 48h, disaster stop -{stop}%', run(E, 48, stop))
for tp in (6, 10, 15):
    show(f'hold 72h, stop -15%, sell all at +{tp}%', run(E, 72, 15, tp))
print()
print('3. who and when')
show('market-wide (BTC also fell >= 4% in 24h)', run(events(0.08, 0.20, mask=(btc24 <= -0.04)[None, :]), 48))
show('single-coin (BTC fell < 4%)', run(events(0.08, 0.20, mask=(btc24 > -0.04)[None, :]), 48))
mm = np.zeros((C, 1), bool); mm[list(MAJ)] = True
show('10 majors only', run(events(0.08, 0.20, mask=mm), 48))
show('the other 20', run(events(0.08, 0.20, mask=~mm), 48))
show('BTC regime bearish at entry', run(events(0.08, 0.20, mask=F['bear'][None, :]), 48))
show('BTC regime OK at entry', run(events(0.08, 0.20, mask=~F['bear'][None, :]), 48))
turn = F['green'] & F['rsi_up']
show('entered on a green hour with RSI rising', run(events(0.08, 0.20, mask=turn), 48))
print()

print('5. portfolio: each trade = a fixed slice of equity, at most `cap` open, 48h hold, stop -15%')
for p, q in ((0.08, 0.20), (0.08, 0.15), (0.08, 0.10), (0.05, 0.20)):
    for slice_, cap in ((0.10, 5), (0.20, 3)):
        evs = sorted(events(p, q), key=lambda e: e[1])
        eq, open_, curve, taken = 1.0, [], [], 0
        t_idx, pnl_at = {}, {}
        for k, i in evs:
            open_ = [x for x in open_ if x > i]
            if len(open_) >= cap:
                continue
            j, kind, r = hold(k, i, 48, 15)
            open_.append(j); taken += 1
            pnl_at.setdefault(j, []).append(r * slice_)
        e, peak, dd, by = 1.0, 1.0, 0.0, {}
        for j in sorted(pnl_at):
            for r in pnl_at[j]:
                e *= (1 + r)
            peak = max(peak, e); dd = max(dd, 1 - e / peak)
            by[int(year[j])] = e
        prev, ys = 1.0, []
        for y in sorted(by):
            ys.append(f'{y % 100}:{by[y] / prev - 1:+.0%}'); prev = by[y]
        yrs = (n - i0) / 24 / 365
        print(f'fall {p:.0%} / OI {q:.0%}, slice {slice_:.0%} x max {cap}:  trades {taken:>4}  total {e - 1:+6.0%}  '
              f'cagr {e ** (1 / yrs) - 1:+5.0%}  max dd {dd:4.0%} | {" ".join(ys)}')
