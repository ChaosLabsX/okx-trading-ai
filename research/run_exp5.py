"""2026-11-03: stricter BTC regime filters (fewer trades in hostile markets) against the
live rule (block when BTC 4H close < EMA50 AND 4H RSI < 45). Five windows."""
import sys
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
import replay as R

sc = R.sc
WINDOWS = R.WINDOWS + [('w_live', '2026-10-09 00:00')]


def rule(kind):
    def bear(btc, T, cache):
        if T not in cache:
            b = btc.closed_4h(T, 100)
            if not b or len(b) < 60:
                cache[T] = False
            else:
                ema, rsi = sc.ema_array(b, 50)[-1], sc.calc_rsi(b)
                below = b[-1] < ema
                cache[T] = {'live (below EMA50 and RSI<45)': below and rsi is not None and rsi < 45,
                            'below EMA50 at all':             below,
                            'below EMA50 or RSI<45':          below or (rsi is not None and rsi < 45),
                            'RSI<50':                         rsi is not None and rsi < 50,
                            'below EMA50 and RSI<50':         below and rsi is not None and rsi < 50}[kind]
        return cache[T]
    return bear


KINDS = ['live (below EMA50 and RSI<45)', 'below EMA50 and RSI<50', 'RSI<50', 'below EMA50 at all',
         'below EMA50 or RSI<45']
res, alltr = defaultdict(dict), defaultdict(list)
orig = R.btc_bear
for w, tf in WINDOWS:
    coins = R.load(w)
    ev = [e for c in coins.values() for e in R.events_for(c)]
    for k in KINDS:
        R.btc_bear = rule(k)
        t, _ = R.portfolio(coins, ev, R.utc(tf), {'be_frac': 0.7})
        res[k][w] = R.stats(t); alltr[k] += t
    print(f'  {w} done', flush=True)
R.btc_bear = orig
base = res[KINDS[0]]
wn = [w for w, _ in WINDOWS]
print(f"\n{'block new trades when BTC 4H is':<34}{'trades':>7}{'net $':>9}{'$/trade':>9}{'maxDD':>7}   "
      + ''.join(f'{w:>9}' for w in wn) + '   better (of 5)')
for k in KINDS:
    s = R.stats(alltr[k])
    better = sum(1 for w in wn if res[k][w]['net'] > base[w]['net'] + 1e-9)
    print(f"{k:<34}{s['n']:>7}{s['net']:>+9.2f}{s['avg']:>+9.3f}{s['dd']:>7.1f}   "
          + ''.join(f"{res[k][w]['net']:>+9.2f}" for w in wn) + f'   {better}')
