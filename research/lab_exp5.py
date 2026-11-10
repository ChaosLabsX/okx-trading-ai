"""Lab experiment 5: does futures-market data identify the dips worth buying?

The idea worth testing (it is what desks mean by "capitulation"): a fall that comes with a
collapse in open interest is forced selling - leveraged longs being liquidated - and forced
selling ends; a fall where open interest holds or grows is sellers adding, which need not.
Funding going negative says shorts are now the crowded side. If any of that is real, the
return AFTER such moments should beat the return after an ordinary fall of the same size.

30 liquid coins with perpetual futures, hourly, 2024-01 -> 2026-11. For each event type:
average return over the next 12/24/48/72 hours (from the next hour's open, before costs),
set against the same coins' average over ALL hours; then the same events traded with the
live exits, after 0.30% costs. One event per coin per 24h. `days` = distinct calendar days,
the honest sample size, since a market-wide flush hits every coin in the same hour.
"""
import os, sys, time
import numpy as np
import lab
from lab_feat import features

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
H = lab.H
syms = sorted(f[:-4] for f in os.listdir(os.path.join(HERE, 'derivs')) if f.endswith('.npy') and '.fund' not in f)
mk = lab.Market(only={s + 'USDT' for s in syms})
with np.errstate(all='ignore'):
    F = features(mk)
C, n = mk.c.shape
c, o = mk.c, mk.o

oi, fund, ls, taker = (np.full((C, n), np.nan) for _ in range(4))
for s in syms:
    k = mk.ix[s + 'USDT']
    a = np.load(os.path.join(HERE, 'derivs', f'{s}.npy'))
    idx = ((a[:, 0] - mk.t0) // H).astype(int)
    okk = (idx >= 0) & (idx < n)
    oi[k, idx[okk]], ls[k, idx[okk]], taker[k, idx[okk]] = a[okk, 1], a[okk, 3], a[okk, 4]
    fp = os.path.join(HERE, 'derivs', f'{s}.fund.npy')
    if os.path.exists(fp):
        f = np.load(fp)
        j = np.searchsorted(f[:, 0], mk.t + H, side='right') - 1     # last print known at each bar's close
        fund[k] = np.where(j >= 0, f[np.maximum(j, 0), 1] * 100, np.nan)
for arr in (oi, ls):                                   # short gaps in the archive: carry the last value
    for k in range(C):
        last = np.nan
        for i in range(n):
            if np.isnan(arr[k, i]): arr[k, i] = last
            else: last = arr[k, i]

chg = lambda x, N: np.where(np.arange(n)[None, :] >= N, x / np.roll(x, N, axis=1) - 1, np.nan)
ret24, ret4, oi24, oi4 = chg(c, 24), chg(c, 4), chg(oi, 24), chg(oi, 4)
rsi_ = F['rsi']
turn = F['green'] & F['rsi_up']
print(f'{C} coins, {lab.fmt(mk.t[0])} -> {lab.fmt(mk.t[-1])}; open interest known for '
      f'{np.mean(~np.isnan(oi[:, 24 * 45:])):.0%} of coin-hours')

HZ = (12, 24, 48, 72)
fwd = {}
for hz in HZ:
    x = np.full((C, n), np.nan)
    x[:, :n - hz - 1] = c[:, hz + 1:] / o[:, 1:n - hz] - 1
    fwd[hz] = x
i0 = 24 * 45
base = {hz: np.nanmean(fwd[hz][:, i0:]) * 100 for hz in HZ}
print('all hours, average next-N-hour return: ' + '  '.join(f'{hz}h {base[hz]:+.2f}%' for hz in HZ) + '\n')

EVENTS = {
    'fall >= 8% in 24h (price only)':                 ret24 <= -0.08,
    '  + open interest down >= 10%  (flush)':         (ret24 <= -0.08) & (oi24 <= -0.10),
    '  + open interest down >= 20%  (hard flush)':    (ret24 <= -0.08) & (oi24 <= -0.20),
    '  + open interest UP >= 5%  (sellers adding)':   (ret24 <= -0.08) & (oi24 >= 0.05),
    '  + flush + funding <= 0':                       (ret24 <= -0.08) & (oi24 <= -0.10) & (fund <= 0),
    '  + flush + turning up (green, RSI up)':         (ret24 <= -0.08) & (oi24 <= -0.10) & turn,
    'fall >= 5% in 24h (price only)':                 ret24 <= -0.05,
    '  + open interest down >= 10%':                  (ret24 <= -0.05) & (oi24 <= -0.10),
    'funding <= -0.02% and RSI <= 35':                (fund <= -0.02) & (rsi_ <= 35),
    'funding <= -0.05%':                              fund <= -0.05,
    'funding >= +0.05% (longs crowded)':              fund >= 0.05,
    'RSI <= 25 (price only)':                         rsi_ <= 25,
    '  + open interest down >= 10% in 24h':           (rsi_ <= 25) & (oi24 <= -0.10),
    'crowd net long: long/short accounts >= 3':       ls >= 3.0,
    'crowd net short: long/short accounts <= 0.8':    ls <= 0.8,
}

print(f"{'event (one per coin per 24h)':<46} {'n':>5} {'days':>5}   " +
      '   '.join(f'{hz}h avg (t)' for hz in HZ))
picked = {}
for name, ev in EVENTS.items():
    ev = ev.copy(); ev[:, :i0] = False
    keep = np.zeros_like(ev)
    for k in range(C):
        last = -10**9
        for i in np.flatnonzero(ev[k]):
            if i - last >= 24:
                keep[k, i] = True; last = i
    picked[name] = keep
    ks, is_ = np.nonzero(keep)
    days = len({int(mk.t[i] // 86_400_000) for i in is_})
    cells = []
    for hz in HZ:
        x = fwd[hz][ks, is_] * 100
        x = x[~np.isnan(x)]
        if len(x) < 5:
            cells.append('     n/a    '); continue
        # t-stat on DAILY means: events in the same day are one observation, not thirty
        dm = {}
        for i, v in zip(is_[~np.isnan(fwd[hz][ks, is_])], x):
            dm.setdefault(int(mk.t[i] // 86_400_000), []).append(v)
        d = np.array([np.mean(v) for v in dm.values()])
        t = (d.mean() - base[hz]) / (d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 2 else float('nan')
        cells.append(f'{x.mean():+6.2f} ({t:+4.1f})')
    print(f'{name:<46} {len(ks):>5} {days:>5}   ' + '   '.join(cells))

print('\nthe same events as trades, after costs (S = each traded alone, B = max 3 open, 1 new per hour):')
for xname, X in (('live exits 2.0/2.5/1.0', dict(tp=2.0, sl=2.5, tr=1.0)), ('wide 4.0/2.5/3.0', dict(tp=4.0, sl=2.5, tr=3.0))):
    print(f'-- {xname}')
    for name in ('fall >= 8% in 24h (price only)', '  + open interest down >= 10%  (flush)',
                 '  + open interest down >= 20%  (hard flush)', '  + flush + funding <= 0',
                 '  + flush + turning up (green, RSI up)', 'funding <= -0.05%', '  + open interest down >= 10% in 24h'):
        setups, book = lab.run(mk, picked[name], F['atr'], X, rank=-ret24, i_from=i0, cooldown=24)
        print(lab.line('S ' + name.strip()[:30], lab.stats(mk, setups)))
        print(lab.line('B ' + name.strip()[:30], lab.stats(mk, book)))
