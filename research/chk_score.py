import sys; sys.stdout.reconfigure(encoding='utf-8')
from collections import defaultdict
import replay as R
agg = defaultdict(list)
for w, tf in R.WINDOWS:
    coins = R.load(w); T_FROM = R.utc(tf)
    ev40 = [e for c in coins.values() for e in R.events_for(c, score_bar=4.0)]
    for label, v in (('4.0 bar', {}), ('4.0 bar + BE 70%', {'be_frac': 0.7})):
        t, _ = R.portfolio(coins, ev40, T_FROM, v)
        for x in t:
            agg[(label, 'score 4.0 trades' if x['score'] < 4.5 else 'score >= 4.5 trades', w)].append(x['frac'] * 100)
    # every 4.0-score setup traded on its own, no portfolio effects
    solo = [R.simulate(coins[e['coin']], e['entry_i'], R.exits(e, 2.0, 2.5, 1.0)) for e in ev40 if e['score'] < 4.5 and e['T'] >= T_FROM]
    agg[('solo', 'every score-4.0 setup alone', w)] = [s[2] * 100 for s in solo if s]
    solo45 = [R.simulate(coins[e['coin']], e['entry_i'], R.exits(e, 2.0, 2.5, 1.0)) for e in ev40 if e['score'] >= 4.5 and e['T'] >= T_FROM]
    agg[('solo', 'every score>=4.5 setup alone', w)] = [s[2] * 100 for s in solo45 if s]
print(f"{'run':<18}{'group':<32}" + ''.join(f'{w:>16}' for w, _ in R.WINDOWS) + '      all')
keys = sorted({(a, b) for a, b, _ in agg})
for a, b in keys:
    cells, allv = '', []
    for w, _ in R.WINDOWS:
        v = agg.get((a, b, w), []); allv += v
        cells += f"{(sum(v) / len(v) if v else 0):>+9.3f}% ({len(v):>3})"
    print(f'{a:<18}{b:<32}{cells}   {sum(allv) / len(allv):+.3f}% ({len(allv)})')
