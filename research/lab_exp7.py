"""Lab experiment 7: the crash-rebound (flush) rule as it will run live - before real money.

Rule: coin down >= 8% over 24h while its futures open interest, in COINS, fell >= 15%;
buy at the next hour's open, sell 48h later. One trade per coin per 72h.

Two questions the live version needs answered:
  1. Where does the protective ("disaster") stop go? It must exist - a hack or a
     delisting can take a coin -60% in an hour - but lab_exp6 showed a stop near the
     price gives the edge away, because after a flush the price whips before it turns.
  2. How often does it fire, and how many positions pile up at once? That sets the
     open-position cap and tells the owner what "trades now" means in practice.

Run on both datasets: 2024-2026 (used to choose the rule) and 2022-2023 (not used).
usage: lab_exp7.py
"""
import os, sys, time
import numpy as np
import lab

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
H = lab.H


def load(bars, der):
    have = {f[:-4] for f in os.listdir(os.path.join(HERE, bars))}
    syms = sorted(f[:-4] for f in os.listdir(os.path.join(HERE, der))
                  if f.endswith('.npy') and '.fund' not in f and f[:-4] + 'USDT' in have)
    mk = lab.Market(bars, only={s + 'USDT' for s in syms})
    C, n = mk.c.shape
    oi = np.full((C, n), np.nan)
    for s in syms:
        k = mk.ix[s + 'USDT']
        a = np.load(os.path.join(HERE, der, f'{s}.npy'))
        idx = ((a[:, 0] - mk.t0) // H).astype(int)
        ok = (idx >= 0) & (idx < n)
        oi[k, idx[ok]] = a[ok, 1]
        last = np.nan
        for i in range(n):
            if np.isnan(oi[k, i]): oi[k, i] = last
            else: last = oi[k, i]
    oi = oi / mk.c                                          # dollars -> coins
    with np.errstate(all='ignore'):
        chg = lambda x, N: np.where(np.arange(n)[None, :] >= N, x / np.roll(x, N, axis=1) - 1, np.nan)
        return mk, chg(mk.c, 24), chg(oi, 24)


def events(mk, ret24, oi24, fall=0.08, drop=0.15, cooldown=72):
    ev = (ret24 <= -fall) & (oi24 <= -drop)
    ev[:, :24 * 45] = False
    out = []
    for k in range(ev.shape[0]):
        last = -10**9
        for i in np.flatnonzero(ev[k]):
            if i - last >= cooldown and i + 2 < mk.n:
                out.append((k, i)); last = i
    return sorted(out, key=lambda e: e[1])


def trade(mk, k, i, hours=48, stop=None):
    """Net result (fraction), exit bar, and the worst low reached, for one trade."""
    o, h, l, c = mk.o[k], mk.h[k], mk.l[k], mk.c[k]
    ie = i + 1
    e = o[ie] * (1 + lab.SLIP)
    net = lambda px: (px * (1 - lab.SLIP) - e) / e - lab.FEE * (1 + px * (1 - lab.SLIP) / e)
    end = min(ie + hours, mk.n) - 1
    worst = 0.0
    for j in range(ie, end + 1):
        worst = min(worst, l[j] / e - 1)
        if stop and l[j] <= e * (1 - stop / 100):
            px = e * (1 - stop / 100)
            return net(min(o[j], px) if j > ie else px), j, worst
    return net(c[end]), end, worst


for label, bars, der in (('2024-2026 (used to choose the rule)', 'bars_1h', 'derivs'),
                         ('2022-2023 (not used)', 'bars_1h_oos', 'derivs_oos')):
    mk, ret24, oi24 = load(bars, der)
    E = events(mk, ret24, oi24)
    months = (mk.n - 24 * 45) / 24 / 30.4
    days = {int(mk.t[i] // 86_400_000) for _, i in E}
    print(f'\n=== {label}: {len(mk.syms)} coins, {len(E)} trades over {months:.0f} months '
          f'({len(E) / months:.1f} a month, on {len(days)} different days = {len(days) / months:.1f} trading days a month)')
    for stop in (None, 50, 40, 30, 25, 20, 15):
        r = np.array([trade(mk, k, i, 48, stop)[0] for k, i in E]) * 100
        hit = sum(1 for k, i in E if stop and trade(mk, k, i, 48, stop)[1] < min(i + 1 + 48, mk.n) - 1
                  and trade(mk, k, i, 48, None)[2] <= -stop / 100)
        win, loss = r[r > 0].sum(), -r[r <= 0].sum()
        print(f"  stop {'none' if stop is None else f'-{stop}%':>5}: avg {r.mean():+5.2f}%  median {np.median(r):+5.2f}%  "
              f"win {np.mean(r > 0):4.0%}  PF {win / loss:4.2f}  worst {r.min():+6.1f}%  stopped out {hit:>3}")
    worst = np.array([trade(mk, k, i, 48, None)[2] for k, i in E]) * 100
    print('  deepest point reached while held (no stop): ' + '  '.join(
        f'<= {-x}%: {np.sum(worst <= x)}' for x in (-10, -15, -20, -25, -30, -40)))
    # how many would be open at once (48h holds), and the busiest day
    opens = sorted(i for _, i in E)
    conc = max(sum(1 for j in opens if i - 48 < j <= i) for i in opens) if opens else 0
    per_day = {}
    for _, i in E:
        per_day[int(mk.t[i] // 86_400_000)] = per_day.get(int(mk.t[i] // 86_400_000), 0) + 1
    busiest = max(per_day.items(), key=lambda kv: kv[1]) if per_day else (0, 0)
    print(f"  most positions open at once: {conc}; busiest day: {busiest[1]} signals "
          f"({time.strftime('%Y-%m-%d', time.gmtime(busiest[0] * 86400))})")
    # what a cap of N open positions does to the result (first come, first served)
    for cap in (2, 3, 4, 6):
        open_until, taken = [], []
        for k, i in E:
            open_until = [x for x in open_until if x > i]
            if len(open_until) >= cap:
                continue
            r, j, _ = trade(mk, k, i, 48, 30)
            open_until.append(j); taken.append(r * 100)
        taken = np.array(taken)
        print(f'  cap {cap} open (stop -30%): {len(taken)} trades, avg {taken.mean():+.2f}%, '
              f'total {taken.sum():+.0f}% of one stake, {len(taken) / months:.1f} a month')
