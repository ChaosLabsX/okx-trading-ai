"""Lab experiment 3: is the multi-day momentum result of lab_exp2.py real, or an artefact of
testing on a coin list chosen in 2026?  Same rules, but on a POINT-IN-TIME universe: every
Binance USDT pair (641, delisted ones included), and on each day only the N most traded
coins of the previous 30 days are eligible. Nothing about the future picks the coins.

Prints, for each rule: total, CAGR, max drawdown, Sharpe, time invested, trade stats, by year.
usage: lab_exp3.py [N=50]"""
import sys
import numpy as np
import lab
from lab_d1 import Daily, backtest, report, bench

sys.stdout.reconfigure(encoding='utf-8')
N_UNI = int(sys.argv[1]) if len(sys.argv) > 1 else 50
mk = Daily('bars_all_1d')
C, n = mk.c.shape
c, o, h, l, v = mk.c, mk.o, mk.h, mk.l, mk.v
START = 120
print(f'{C} pairs, {n} days, {mk.day[0]} -> {mk.day[-1]}; results from {mk.day[START]}; '
      f'universe = top {N_UNI} by 30-day volume, listed >= 60 days')

with np.errstate(all='ignore'):
    atr = lab.atr_pct(h, l, c)
e20, e50 = lab.ema(c, 20), lab.ema(c, 50)
ret = lambda N: np.where(np.arange(n)[None, :] >= N, c / np.roll(c, N, axis=1) - 1, np.nan)
vol30 = lab.roll(v, 30, np.mean)
age = np.cumsum(~np.isnan(c), axis=1) >= 60
vr = np.where(age & ~np.isnan(c), vol30, -1.0)
vr[np.isnan(vr)] = -1.0
order = np.argsort(-vr, axis=0)
rk_v = np.empty_like(order); np.put_along_axis(rk_v, order, np.arange(C)[:, None], axis=0)
uni = (rk_v < N_UNI) & (vr > 0)
b = mk.ix['BTCUSDT']
btc_up = c[b] > e50[b]

dr = np.nan_to_num(c[:, 1:] / c[:, :-1] - 1)
btc_eq = np.ones(n); uni_eq = np.ones(n)
for d in range(1, n):
    btc_eq[d] = btc_eq[d - 1] * (1 + dr[b, d - 1])
    m = uni[:, d - 1]
    uni_eq[d] = uni_eq[d - 1] * (1 + (dr[m, d - 1].mean() if m.any() else 0))
print(bench(mk, 'BTC buy & hold', btc_eq, START, n))
print(bench(mk, f'top-{N_UNI} equal-weight, hold', uni_eq, START, n))
print()


def xs(L, K, keep, gate, gate_exit, sl=3.0, risk=None, trend=True):
    mom = ret(L)
    elig = uni & (mom > 0) & ((c > e50) if trend else True)
    score = np.where(elig, mom, -np.inf)
    od = np.argsort(-score, axis=0)
    rk = np.empty_like(od); np.put_along_axis(rk, od, np.arange(C)[:, None], axis=0)
    ent = elig & (rk < K)
    ex = ~elig | (rk >= keep)
    P = dict(slots=K, sl=sl, trail=0, exit=ex, gate=gate, gate_exit=gate_exit, atr=atr, risk=risk)
    return backtest(mk, ent, mom, P, START)


print('--- XS rotation: hold the K strongest by L-day return, above EMA50; BTC > EMA50 gate, exit on flip ---')
for K in (3, 5):
    for L in (3, 5, 7, 10, 14, 21, 28):
        print(report(mk, f'XS L{L} K{K}', xs(L, K, 2 * K, btc_up, True)))
    print()
print('--- same, gate only blocks new buys (no forced exit) ---')
for L in (7, 14, 28):
    print(report(mk, f'XS L{L} K3 gate-entry-only', xs(L, 3, 6, btc_up, False)))
print('--- same, no gate at all ---')
for L in (7, 14, 28):
    print(report(mk, f'XS L{L} K3 no gate', xs(L, 3, 6, None, False)))
print()
print('--- breakout / trend state on the same universe, BTC gate + exit on flip ---')
for N in (10, 20, 30):
    hi = lab.roll(c, N, np.max); lo = lab.roll(c, max(5, N // 2), np.min)
    P = dict(slots=3, sl=2.5, trail=0, exit=c < lo, gate=btc_up, gate_exit=True, atr=atr)
    print(report(mk, f'DON {N}/{max(5, N // 2)}', backtest(mk, uni & (c > hi), ret(14), P, START)))
state = uni & (c > e20) & (e20 > e50) & (ret(14) > 0)
P = dict(slots=3, sl=2.5, trail=0, exit=c < e20, gate=btc_up, gate_exit=True, atr=atr)
print(report(mk, 'EMA 20>50, r14>0', backtest(mk, state, ret(14), P, START)))
print()
print('--- risk-based size: each position sized to lose 1% of equity at its stop (5 slots) ---')
for L in (7, 14, 28):
    print(report(mk, f'XS L{L} K5 risk 1%', xs(L, 5, 10, btc_up, True, risk=0.01)))
