import os, sys, time
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
import replay as R

V = {'baseline (live rules)': {}}
for a in (0.75, 1.0, 1.25, 1.5, 1.75, 2.0):
    V[f'BE after +{a} ATR'] = {'be_atr': a}
for f in (0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
    V[f'BE at {int(f * 100)}% of way to TP'] = {'be_frac': f}
for b in (3.5, 4.0, 4.5, 5.0):
    V[f'score >= {b}'] = {'_bar': b}
V['score 4.0 + BE 70% to TP'] = {'_bar': 4.0, 'be_frac': 0.7}

res, alltr, days_total = defaultdict(dict), defaultdict(list), 0.0
detail = defaultdict(lambda: defaultdict(int))
for w, tf in R.WINDOWS:
    t0 = time.time()
    coins = R.load(w)
    T_FROM = R.utc(tf)
    days = (coins['BTC-USDT'].now - T_FROM) / (24 * R.H); days_total += days
    ev = {}
    for name, v in V.items():
        bar = v.get('_bar', 4.5)
        if bar not in ev:
            ev[bar] = [e for c in coins.values() for e in R.events_for(c, score_bar=bar)]
        t, b = R.portfolio(coins, ev[bar], T_FROM, v)
        res[name][w] = {**R.stats(t), 'days': days}
        alltr[name] += t
        for x in t:
            detail[name][x['type']] += 1
    print(f'  {w}: {time.time() - t0:.0f}s', flush=True)

base = res['baseline (live rules)']
print(f"\n{'variant':<30}{'trades':>7}{'net $':>9}{'$/trade':>9}{'PF':>6}{'maxDD':>7}{'win%':>6}"
      f"   per-window net (trades)                                   avg>=base net>base   exits")
for name, per in res.items():
    s = R.stats(alltr[name])
    cells = '  '.join(f"{per[w]['net']:+7.2f} ({per[w]['n']:>3})" for w, _ in R.WINDOWS)
    better = sum(1 for w, _ in R.WINDOWS if per[w]['avg'] >= base[w]['avg'])
    netb = sum(1 for w, _ in R.WINDOWS if per[w]['net'] > base[w]['net'])
    print(f"{name:<30}{s['n']:>7}{s['net']:>+9.2f}{s['avg']:>+9.3f}{s['pf']:>6.2f}{s['dd']:>7.1f}{s['wr'] * 100:>6.0f}"
          f"   {cells}   {better}/4      {netb}/4    {dict(detail[name])}")
