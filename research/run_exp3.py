"""2026-10-19: where the break-even stop should sit once triggered (70% of the way to
TP) — at entry (live 10-09 to 10-19), at exact entry+fees, or entry+fees plus a
slippage margin (live since 10-19). Live score bar (4.0). Same trades, same paths:
only the stop level after the trigger differs, so the comparison is paired."""
import sys, time
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
import replay as R

FEES = (1 + R.sc.OKX_FEE_RATE) / (1 - R.sc.OKX_FEE_RATE)
V = {
    'no break-even stop':                 {},
    'BE stop at entry (10-09..10-19)':     {'be_frac': 0.7, 'be_stop': 1.0},
    'BE stop at entry + fees (exact $0)':  {'be_frac': 0.7, 'be_stop': FEES},
    'BE stop at entry + fees + 0.1% (live)': {'be_frac': 0.7},
    'BE stop at entry + fees + 0.2%':      {'be_frac': 0.7, 'be_stop': FEES * 1.002},
}
res, alltr, be_exits = defaultdict(dict), defaultdict(list), defaultdict(list)
for w, tf in R.WINDOWS:
    t0 = time.time()
    coins = R.load(w)
    T_FROM = R.utc(tf)
    ev = [e for c in coins.values() for e in R.events_for(c)]
    for name, v in V.items():
        t, _ = R.portfolio(coins, ev, T_FROM, v)
        res[name][w] = R.stats(t)
        alltr[name] += t
        be_exits[name] += [x['frac'] * 100 for x in t if x['type'] == 'be']
    print(f'  {w}: {time.time() - t0:.0f}s', flush=True)

base = res['BE stop at entry (10-09..10-19)']
print(f"\n{'variant':<40}{'trades':>7}{'net $':>9}{'maxDD':>7}{'BE exits':>9}{'avg BE exit':>12}"
      f"   per-window net                         net>at-entry")
for name, per in res.items():
    s = R.stats(alltr[name])
    b = be_exits[name]
    cells = '  '.join(f"{per[w]['net']:+7.2f}" for w, _ in R.WINDOWS)
    better = sum(1 for w, _ in R.WINDOWS if per[w]['net'] > base[w]['net'])
    print(f"{name:<40}{s['n']:>7}{s['net']:>+9.2f}{s['dd']:>7.1f}{len(b):>9}"
          f"{(sum(b) / len(b) if b else 0):>+11.3f}%   {cells}     {better}/4")
