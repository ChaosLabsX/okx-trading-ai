import json, os, sys, time
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
import replay as R

VARIANTS = {
    'baseline (live rules)':          {},
    'trail 1.5x ATR':                 {'tr': 1.5},
    'trail 2.0x ATR':                 {'tr': 2.0},
    'TP 1.5x ATR':                    {'tp': 1.5},
    'TP 2.5x ATR':                    {'tp': 2.5},
    'TP 3.0x ATR':                    {'tp': 3.0},
    'TP 2.5 + trail 1.5':             {'tp': 2.5, 'tr': 1.5},
    'SL 2.0x ATR (tighter)':          {'sl': 2.0},
    'SL 3.0x ATR (wider)':            {'sl': 3.0},
    'break-even after +1.0 ATR':      {'be_atr': 1.0},
    'break-even after +1.5 ATR':      {'be_atr': 1.5},
    'time stop 48h':                  {'time_stop_h': 48},
    'time stop 96h':                  {'time_stop_h': 96},
    'max open 4':                     {'max_open': 4},
    'max open 5':                     {'max_open': 5},
    'score >= 4.0':                   {'_bar': 4.0},
}

res, alltr, days_total = defaultdict(dict), defaultdict(list), 0.0
live_check = None
for w, tf in R.WINDOWS:
    t0 = time.time()
    coins = R.load(w)
    T_FROM = R.utc(tf)
    days = (coins['BTC-USDT'].now - T_FROM) / (24 * R.H); days_total += days
    ev = {None: [e for c in coins.values() for e in R.events_for(c)]}
    ev[4.0] = [e for c in coins.values() for e in R.events_for(c, score_bar=4.0)]
    for name, v in VARIANTS.items():
        t, b = R.portfolio(coins, ev[v.get('_bar')], T_FROM, v)
        res[name][w] = {**R.stats(t), 'days': days}
        alltr[name] += t
        if name.startswith('baseline'):
            ref = sum(x['sl'] for x in t) / len(t)
            res['risk-based sizing (same trades)'][w] = {**R.stats(t, lambda x: ref / x['sl']), 'days': days}
            alltr['risk-based sizing (same trades)'] += [dict(x, frac=x['frac'] * ref / x['sl']) for x in t]
            if w == 'w_now':
                live_check = t
    print(f'  {w}: {len(coins)} coins, {days:.0f} days, {len(ev[None])} candidate events, {time.time() - t0:.0f}s', flush=True)

base = res['baseline (live rules)']
print(f"\n{'variant':<34}{'trades':>7}{'/wk':>6}{'net $':>9}{'$/trade':>9}{'PF':>6}{'maxDD':>7}"
      f"   per-window net (trades)                                   avg>=base  net>base")
for name, per in res.items():
    s = R.stats(alltr[name])
    cells = '  '.join(f"{per[w]['net']:+7.2f} ({per[w]['n']:>3})" for w, _ in R.WINDOWS)
    better = sum(1 for w, _ in R.WINDOWS if per[w]['avg'] >= base[w]['avg'])
    netb = sum(1 for w, _ in R.WINDOWS if per[w]['net'] > base[w]['net'])
    print(f"{name:<34}{s['n']:>7}{s['n'] / days_total * 7:>6.2f}{s['net']:>+9.2f}{s['avg']:>+9.3f}{s['pf']:>6.2f}{s['dd']:>7.1f}"
          f"   {cells}   {better}/4       {netb}/4")

with open(os.path.join(R.HERE, 'live_check.json'), 'w') as f:
    json.dump(live_check, f)
