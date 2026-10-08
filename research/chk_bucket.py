import sys; sys.stdout.reconfigure(encoding='utf-8')
from collections import defaultdict
import replay as R
B = lambda s: '4.0' if s < 4.5 else '4.5' if s < 5 else '5.0' if s < 5.5 else '5.5' if s < 6 else '6.0+'
agg = defaultdict(list)
for w, tf in R.WINDOWS:
    coins = R.load(w); T_FROM = R.utc(tf); btc = coins['BTC-USDT']; bc = {}
    ev = sorted((e for c in coins.values() for e in R.events_for(c, score_bar=4.0)), key=lambda e: e['T'])
    last = {}
    for e in ev:
        if e['T'] < T_FROM or R.btc_bear(btc, e['T'], bc):
            continue
        if e['T'] - last.get(e['coin'], -10**15) < R.SUPPRESS:
            continue
        last[e['coin']] = e['T']
        for be in (None, 0.7):
            s = R.simulate(coins[e['coin']], e['entry_i'], R.exits(e, 2.0, 2.5, 1.0), be_frac=be)
            if s:
                agg[(be, B(e['score']), w)].append(s[2] * 100)
for be in (None, 0.7):
    print(f"\n{'no BE' if be is None else 'BE at 70% to TP'} — every setup alone, BTC filter on, one per coin per 4h; avg % per trade (n)")
    print(f"{'score':<8}" + ''.join(f'{w:>17}' for w, _ in R.WINDOWS) + '        all')
    for b in ('4.0', '4.5', '5.0', '5.5', '6.0+'):
        cells, allv = '', []
        for w, _ in R.WINDOWS:
            v = agg.get((be, b, w), []); allv += v
            cells += f"{(sum(v) / len(v) if v else 0):>+10.3f}% ({len(v):>3})"
        print(f'{b:<8}{cells}   {sum(allv) / len(allv):+.3f}% ({len(allv)})')
