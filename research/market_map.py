"""What kind of market was it? Monthly return of BTC and of the equal-weight basket of the
scanned coins, plus how much of each month BTC spent in the 'bearish' state the live
regime filter blocks (4H close under the 4H EMA50 and 4H RSI under 45).
Needs research/bars_1d and research/bars_4h (fetch_bars.py)."""
import glob, os, sys, time
import numpy as np

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
ym = lambda ms: time.strftime('%Y-%m', time.gmtime(ms / 1000))


def rsi_series(c, period=14):
    d = np.diff(c)
    g, l = np.maximum(d, 0), np.maximum(-d, 0)
    out = np.full(len(c), np.nan)
    ag, al = g[:period].mean(), l[:period].mean()
    out[period] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(period, len(d)):
        ag = (ag * (period - 1) + g[i]) / period
        al = (al * (period - 1) + l[i]) / period
        out[i + 1] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def ema(c, period):
    k, out = 2 / (period + 1), np.empty(len(c))
    out[0] = c[0]
    for i in range(1, len(c)):
        out[i] = c[i] * k + out[i - 1] * (1 - k)
    return out


d1 = {os.path.basename(f)[:-4]: np.load(f) for f in glob.glob(os.path.join(HERE, 'bars_1d', '*.npy'))}
btc = d1['BTCUSDT']
months = sorted({ym(t) for t in btc[:, 0]})
b4 = np.load(os.path.join(HERE, 'bars_4h', 'BTCUSDT.npy'))
bear = (b4[:, 4] < ema(b4[:, 4], 50)) & (rsi_series(b4[:, 4]) < 45)
m4 = np.array([ym(t) for t in b4[:, 0]])

print('month     BTC    alts(eq-wt)  coins   BTC bearish share   BTC close')
for m in months:
    idx = [i for i, t in enumerate(btc[:, 0]) if ym(t) == m]
    r_btc = btc[idx[-1], 4] / btc[idx[0], 1] - 1
    rs = []
    for s, a in d1.items():
        if s == 'BTCUSDT':
            continue
        j = [i for i, t in enumerate(a[:, 0]) if ym(t) == m]
        if len(j) >= 20:
            rs.append(a[j[-1], 4] / a[j[0], 1] - 1)
    sh = bear[m4 == m].mean() if (m4 == m).any() else float('nan')
    print(f'{m}  {r_btc:>+6.1%}   {np.mean(rs) if rs else float("nan"):>+7.1%}     {len(rs):>3}        '
          f'{sh:>5.0%}          {btc[idx[-1], 4]:>9,.0f}')
