"""2026-11-03 review: ideas not tested before, against the live rules (4.0 bar, break-even
stop at 70% to entry+fees). Four 82-day windows plus the live period (Oct 9 - Nov 3).
Also a feature table: every setup traded alone (BTC filter on, one per coin per 4h),
average result by feature bin, per window - to see which conditions are consistently
bad before any filter is tried in the portfolio."""
import sys, time
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
import replay as R

WINDOWS = R.WINDOWS + [('w_live', '2026-10-09 00:00')]
LIVE = {'be_frac': 0.7}
V = {'live rules': dict(LIVE)}
for f in (0.33, 0.67, 1.0):
    V[f'sell {int(f * 100)}% at take-profit'] = {**LIVE, 'tp_frac': f}
for m in (1.75, 2.0, 2.25, 3.0):
    V[f'stop {m}x ATR'] = {**LIVE, 'sl': m}
for m in (1.5, 2.5):
    V[f'take-profit {m}x ATR'] = {**LIVE, 'tp': m}
for h in (24, 72):
    V[f'{h}h pause on a coin after its stop'] = {**LIVE, 'sl_cooldown_h': h}
for x in (2.5, 3.0):
    V[f'volume >= {x}x'] = {**LIVE, 'filter': (lambda e, x=x: e['vol'] >= x)}
for x in (20, 25):
    V[f'skip 1H RSI <= {x}'] = {**LIVE, 'filter': (lambda e, x=x: e['rsi'] is None or e['rsi'] > x)}
for x in (0.0, -0.1):
    V[f'skip price below lower band (%b <= {x})'] = {**LIVE, 'filter': (lambda e, x=x: e['pct_b'] is None or e['pct_b'] > x)}
for x in (30, 35):
    V[f'skip 4H RSI <= {x}'] = {**LIVE, 'filter': (lambda e, x=x: e['rsi4'] is None or e['rsi4'] > x)}
for x in (-8, -12):
    V[f'skip after a 24h fall of {x}% or more'] = {**LIVE, 'filter': (lambda e, x=x: e['chg24'] is None or e['chg24'] > x)}

BINS = {
    'rsi':   [(0, 20), (20, 25), (25, 30), (30, 100)],
    'rsi4':  [(0, 30), (30, 35), (35, 40), (40, 100)],
    'pct_b': [(-9, -0.1), (-0.1, 0.0), (0.0, 0.1), (0.1, 9)],
    'chg24': [(-99, -12), (-12, -8), (-8, -4), (-4, 99)],
    'vol':   [(2.0, 2.5), (2.5, 3.0), (3.0, 4.0), (4.0, 999)],
    'score': [(4.0, 4.5), (4.5, 5.0), (5.0, 6.0), (6.0, 99)],
}

res, alltr, feat = defaultdict(dict), defaultdict(list), defaultdict(lambda: defaultdict(list))
for w, tf in WINDOWS:
    t0 = time.time()
    coins = R.load(w)
    T_FROM = R.utc(tf)
    ev = [e for c in coins.values() for e in R.events_for(c)]
    for name, v in V.items():
        t, _ = R.portfolio(coins, ev, T_FROM, v)
        res[name][w] = R.stats(t)
        alltr[name] += t
    # feature table: each setup alone, BTC filter on, one per coin per 4h, live exits
    btc, bc, last = coins['BTC-USDT'], {}, {}
    for e in sorted(ev, key=lambda e: e['T']):
        if e['T'] < T_FROM or R.btc_bear(btc, e['T'], bc) or e['T'] - last.get(e['coin'], -10**15) < R.SUPPRESS:
            continue
        last[e['coin']] = e['T']
        ex = R.exits(e, R.sc.ATR_TP_MULT, R.sc.ATR_SL_MULT, R.sc.ATR_TRAIL_MULT)
        out = R.simulate(coins[e['coin']], e['entry_i'], ex, be_frac=0.7) if ex else None
        if not out:
            continue
        for k, bins in BINS.items():
            val = e.get(k)
            if val is None:
                continue
            for lo, hi in bins:
                if lo <= val < hi:
                    feat[(k, lo, hi)][w].append(out[2] * 100)
    print(f'  {w}: {time.time() - t0:.0f}s', flush=True)

base = res['live rules']
wn = [w for w, _ in WINDOWS]
print(f"\n{'variant':<44}{'trades':>7}{'net $':>9}{'$/trade':>9}{'maxDD':>7}   "
      + ''.join(f'{w:>9}' for w in wn) + '   better (of 5)')
for name, per in res.items():
    s = R.stats(alltr[name])
    better = sum(1 for w in wn if per[w]['net'] > base[w]['net'] + 1e-9)
    print(f"{name:<44}{s['n']:>7}{s['net']:>+9.2f}{s['avg']:>+9.3f}{s['dd']:>7.1f}   "
          + ''.join(f"{per[w]['net']:>+9.2f}" for w in wn) + f'   {better}')

print('\nFEATURE TABLE - every setup alone (BTC filter on, one per coin per 4h), avg % per trade (n)')
for k, bins in BINS.items():
    print(f'  {k}')
    for lo, hi in bins:
        cells, allv = '', []
        for w in wn:
            v = feat[(k, lo, hi)][w]; allv += v
            cells += f"{(sum(v) / len(v) if v else 0):>+8.2f}({len(v):>3})"
        print(f'    [{lo:>6}, {hi:>6})  {cells}   all {sum(allv) / len(allv) if allv else 0:+.2f}% ({len(allv)})')
