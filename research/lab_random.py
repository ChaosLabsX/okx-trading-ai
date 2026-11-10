"""The yardstick for lab_exp1.py: what does buying at RANDOM moments cost, with the same
exits and the same 0.30% round trip? A rule is only worth something if it beats this.
Random hours in a non-bearish BTC regime, one in 400 coin-hours, five different seeds."""
import sys
import numpy as np
import lab
from lab_feat import features

sys.stdout.reconfigure(encoding='utf-8')
mk = lab.Market()
with np.errstate(all='ignore'):
    F = features(mk)
i0 = 24 * 45
ok = ~F['bear'][None, :] & ~np.isnan(mk.c)
for xname, X in (('live 2.0/2.5/1.0', dict(tp=2.0, sl=2.5, tr=1.0)), ('wide 4.0/2.5/3.0', dict(tp=4.0, sl=2.5, tr=3.0))):
    print(f'=== exits {xname} ===')
    avgs = []
    for seed in range(5):
        rng = np.random.default_rng(seed)
        sig = ok & (rng.random(mk.c.shape) < 1 / 400)
        setups, book = lab.run(mk, sig, F['atr'], X, i_from=i0, portfolio=False)
        s = lab.stats(mk, setups)
        avgs.append(s['avg'])
        print(lab.line(f'S random entries, seed {seed}', s))
    print(f'mean of the five: {np.mean(avgs):+.3f}% per trade\n')
