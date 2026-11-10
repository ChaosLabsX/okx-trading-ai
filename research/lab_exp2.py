"""Lab experiment 2: multi-day trend and momentum rules on daily bars, 2023-01 -> 2026-11,
long-only, 3 equal slots, 0.30% round-trip costs. Pre-specified families:

  DON  breakout: close above the highest close of the prior N days; out under the lowest
       close of the prior N/2 days. (Donchian channel, the classic trend-following entry.)
  EMA  trend state: close > EMA20 > EMA50 and 14-day return > 0; out when close < EMA20.
  XS   rotation: hold the 3 strongest coins by 14- or 28-day return that are above their
       EMA50; replace one only when it drops out of the top 6 or under its EMA50.

each with and without a market gate (new buys only while BTC closes above its 50-day EMA),
and with a hard stop under every position. Benchmarks at the top."""
import sys, time
import numpy as np
import lab
from lab_d1 import Daily, backtest, report, bench

sys.stdout.reconfigure(encoding='utf-8')
mk = Daily()
C, n = mk.c.shape
c, o, h, l, v = mk.c, mk.o, mk.h, mk.l, mk.v
START = 120
print(f'{C} coins, {n} days, {mk.day[0]} -> {mk.day[-1]}; results from {mk.day[START]}')

atr = lab.atr_pct(h, l, c)
e20, e50, e100 = lab.ema(c, 20), lab.ema(c, 50), lab.ema(c, 100)
ret = lambda N: np.where(np.arange(n)[None, :] >= N, c / np.roll(c, N, axis=1) - 1, np.nan)
r7, r14, r28 = ret(7), ret(14), ret(28)
liq = lab.roll(v, 30, np.mean) >= 5e6                      # 30-day average quote volume on Binance
age = np.cumsum(~np.isnan(c), axis=1) >= 100               # enough history for the averages
ok = liq & age
b = mk.ix['BTCUSDT']
btc_up = c[b] > e50[b]
btc_up2 = btc_up & (e20[b] > e50[b])
breadth = np.nansum((c > e50) & ok, axis=0) / np.maximum(ok.sum(axis=0), 1)

# benchmarks
dr = np.nan_to_num(c[:, 1:] / c[:, :-1] - 1)
alt = np.ones(n); btc_eq = np.ones(n); btc_g = np.ones(n)
cnt = np.maximum((~np.isnan(c[:, 1:]) & ~np.isnan(c[:, :-1])).sum(axis=0), 1)
for d in range(1, n):
    alt[d] = alt[d - 1] * (1 + dr[:, d - 1].sum() / cnt[d - 1])
    btc_eq[d] = btc_eq[d - 1] * (1 + dr[b, d - 1])
    btc_g[d] = btc_g[d - 1] * (1 + (dr[b, d - 1] if btc_up[d - 1] else 0))
print(bench(mk, 'BTC buy & hold', btc_eq, START, n))
print(bench(mk, 'all coins equal-weight, hold', alt, START, n))
print(bench(mk, 'BTC only while above its EMA50', btc_g, START, n))
print()

rules = {}
for N in (10, 20, 30, 55):
    hi = lab.roll(c, N, np.max)
    lo = lab.roll(c, max(5, N // 2), np.min)
    rules[f'DON {N}/{max(5, N // 2)}'] = (ok & (c > hi), c < lo)
state = ok & (c > e20) & (e20 > e50) & (r14 > 0)
rules['EMA 20>50, r14>0'] = (state, c < e20)
rules['EMA 20>50, exit < EMA50'] = (state, c < e50)

GATES = {'no gate': None, 'BTC>EMA50': btc_up, 'BTC>EMA50, exit on flip': btc_up, 'breadth>50%': breadth > 0.5}
for gname, gate in GATES.items():
    print(f'--- {gname} ---')
    for name, (ent, ex) in rules.items():
        for sl in (2.5,):
            P = dict(slots=3, sl=sl, trail=0, exit=ex, gate=gate, gate_exit='exit on flip' in gname, atr=atr)
            print(report(mk, f'{name} sl{sl}', backtest(mk, ent, r14, P, START)))
    print()

# cross-sectional rotation
print('--- XS rotation (top 3, keep while in top 6) ---')
for mom_name, mom in (('r14', r14), ('r28', r28), ('r7', r7)):
    for gname, gate in (('no gate', None), ('BTC>EMA50', btc_up), ('BTC>EMA50, exit on flip', btc_up)):
        elig = ok & (c > e50) & (mom > 0)
        score = np.where(elig, mom, -np.inf)
        order = np.argsort(-score, axis=0)
        rk = np.empty_like(order); np.put_along_axis(rk, order, np.arange(C)[:, None], axis=0)
        ent = elig & (rk < 3)
        ex = ~elig | (rk >= 6)
        P = dict(slots=3, sl=3.0, trail=0, exit=ex, gate=gate, gate_exit='exit on flip' in gname, atr=atr)
        print(report(mk, f'XS {mom_name} {gname}', backtest(mk, ent, mom, P, START)))
