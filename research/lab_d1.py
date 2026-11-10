"""
Daily-bar portfolio lab: multi-day trend / momentum rules, long-only spot, 2023-01 onward.

The hourly lab (lab_exp1.py) found no edge in any intraday entry family once 0.30% of
round-trip costs is charged: trades that aim for a couple of percent give a seventh of it
to fees. This engine tests the other end: positions held for days to weeks, where the same
cost is small against the move, and where momentum in crypto is best documented.

Mechanics (no look-ahead):
  - Everything is decided on a daily CLOSE (00:00 UTC) and executed at the next OPEN + slippage.
  - A hard stop sits under each position from entry; it is checked against each day's low
    and fills at the stop (or at the open, if the day opens below it).
  - `slots` equal slices of equity; cash earns nothing. Costs: FEE + SLIP per side.
"""
import glob, os, sys, time
import numpy as np

import lab

HERE = os.path.dirname(os.path.abspath(__file__))
D = 86_400_000
FEE, SLIP = lab.FEE, lab.SLIP


class Daily:
    def __init__(self, folder='bars_1d', only=None, drop=()):
        files = sorted(glob.glob(os.path.join(HERE, folder, '*.npy')))
        raw = {os.path.basename(f)[:-4]: np.load(f) for f in files}
        raw = {k: v for k, v in raw.items() if k not in drop and (not only or k in only)}
        self.syms = sorted(raw)
        self.t0 = int(min(a[0, 0] for a in raw.values()))
        t1 = int(max(a[-1, 0] for a in raw.values()))
        self.n = n = (t1 - self.t0) // D + 1
        self.t = self.t0 + np.arange(n) * D
        C = len(self.syms)
        self.o, self.h, self.l, self.c, self.v = (np.full((C, n), np.nan) for _ in range(5))
        for k, s in enumerate(self.syms):
            a = raw[s]
            idx = ((a[:, 0] - self.t0) // D).astype(int)
            self.o[k, idx], self.h[k, idx], self.l[k, idx], self.c[k, idx], self.v[k, idx] = a[:, 1:6].T
        self.ix = {s: k for k, s in enumerate(self.syms)}
        self.day = [time.strftime('%Y-%m-%d', time.gmtime(t / 1000)) for t in self.t]
        self.year = np.array([time.gmtime(t / 1000).tm_year for t in self.t])


def backtest(mk, entry, rank, P, start=120, end=None):
    """
    entry: bool (C, n) - buy this coin at tomorrow's open.   rank: float (C, n), higher first.
    P: slots, sl (x daily ATR%), trail (x ATR%, chandelier from the highest close; 0 = off),
       exit (bool (C, n): sell at tomorrow's open), gate (bool (n): False = no new entries),
       gate_exit (True: also sell everything when the gate closes), max_hold (days),
       atr (C, n) daily ATR in %.
    Returns dict with equity curve and closed trades.
    """
    n = mk.n if end is None else end
    slots = P['slots']
    cash, pos = 1.0, {}
    eq = np.ones(n)
    trades, exits_next, entries_next = [], set(), []
    exposure = np.zeros(n)
    atr = P['atr']
    gate = P.get('gate')
    exit_sig = P.get('exit')

    def sell(k, px, d, why):
        nonlocal cash
        p = pos.pop(k)
        px *= (1 - SLIP)
        cash += p['qty'] * px * (1 - FEE)
        trades.append({'k': k, 'd0': p['d'], 'd1': d, 'why': why,
                       'r': px * (1 - FEE) / (p['e'] * (1 + FEE)) - 1})

    for d in range(start, n):
        # -- open: yesterday's decisions
        for k in list(exits_next):
            if k in pos and not np.isnan(mk.o[k, d]):
                sell(k, mk.o[k, d], d, pos[k].get('why', 'signal'))
        exits_next.clear()
        equity_open = cash + sum(p['qty'] * mk.o[k, d] for k, p in pos.items() if not np.isnan(mk.o[k, d]))
        for k in entries_next:
            if k in pos or len(pos) >= slots or np.isnan(mk.o[k, d]):
                continue
            e = mk.o[k, d] * (1 + SLIP)
            a = atr[k, d - 1]
            alloc = min(cash, equity_open / slots)
            if P.get('risk'):                     # risk-based size: lose `risk` of equity if the stop is hit
                alloc = min(alloc, P['risk'] * equity_open / (P['sl'] * a / 100))
            if alloc <= 0.005 * equity_open or np.isnan(a):
                continue
            pos[k] = {'qty': alloc / (e * (1 + FEE)), 'e': e, 'd': d, 'peak': e,
                      'stop': e * (1 - P['sl'] * a / 100), 'atr': a}
            cash -= alloc
        entries_next = []
        # -- during the day: stops
        for k in list(pos):
            p = pos[k]
            if np.isnan(mk.l[k, d]):
                continue
            if mk.l[k, d] <= p['stop']:
                sell(k, min(mk.o[k, d], p['stop']), d, 'stop')
        # -- close: mark, trail, decide
        val = 0.0
        for k, p in pos.items():
            cl = mk.c[k, d]
            if np.isnan(cl):
                cl = p['e']
            val += p['qty'] * cl
            if cl > p['peak']:
                p['peak'] = cl
            if P.get('trail'):
                p['stop'] = max(p['stop'], p['peak'] * (1 - P['trail'] * p['atr'] / 100))
            if exit_sig is not None and exit_sig[k, d]:
                p['why'] = 'signal'; exits_next.add(k)
            if P.get('max_hold') and d - p['d'] >= P['max_hold']:
                p['why'] = 'time'; exits_next.add(k)
            if gate is not None and P.get('gate_exit') and not gate[d]:
                p['why'] = 'regime'; exits_next.add(k)
        eq[d] = cash + val
        exposure[d] = val / eq[d] if eq[d] > 0 else 0
        if gate is None or gate[d]:
            free = slots - len(pos) + len(exits_next)
            if free > 0:
                cand = [k for k in np.flatnonzero(entry[:, d]) if k not in pos or k in exits_next]
                cand = [k for k in cand if k not in pos]
                cand.sort(key=lambda k: -rank[k, d] if not np.isnan(rank[k, d]) else 1e9)
                entries_next = cand[:free]
    for k in list(pos):
        sell(k, mk.c[k, n - 1], n - 1, 'open')
    return {'eq': eq, 'trades': trades, 'exposure': exposure, 'start': start, 'n': n}


def report(mk, name, res):
    eq, s, n = res['eq'], res['start'], res['n']
    e = eq[s:n]
    years = (n - s) / 365.0
    total = e[-1] / e[0] - 1
    cagr = (e[-1] / e[0]) ** (1 / years) - 1
    dd = float(np.max(1 - e / np.maximum.accumulate(e)))
    dr = np.diff(e) / e[:-1]
    sharpe = dr.mean() / dr.std() * np.sqrt(365) if dr.std() > 0 else 0
    tr = res['trades']
    r = np.array([t['r'] for t in tr]) * 100 if tr else np.array([0.0])
    win, loss = r[r > 0].sum(), -r[r <= 0].sum()
    yr = []
    for y in sorted(set(mk.year[s:n])):
        idx = np.flatnonzero(mk.year[:n] == y); idx = idx[idx >= s]
        base = eq[idx[0] - 1] if idx[0] > s else eq[s]
        yr.append(f"{y % 100}:{eq[idx[-1]] / base - 1:+.0%}")
    hold = np.mean([t['d1'] - t['d0'] for t in tr]) if tr else 0
    return (f"{name:<40} tot {total:>+6.0%} cagr {cagr:>+5.0%} dd {dd:>4.0%} sh {sharpe:>5.2f} "
            f"exp {res['exposure'][s:n].mean():>4.0%} | n {len(tr):>4} win {np.mean(r > 0):>4.0%} "
            f"avg {r.mean():>+5.1f}% PF {win / loss if loss else 9.99:>4.2f} hold {hold:>4.1f}d | {' '.join(yr)}")


def bench(mk, name, eqv, start, n):
    return report(mk, name, {'eq': eqv, 'trades': [], 'exposure': np.ones(n), 'start': start, 'n': n})
