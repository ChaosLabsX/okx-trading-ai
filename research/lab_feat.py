"""Shared feature set for the lab experiments: every indicator the candidate rules read,
computed once on the hourly grid. Import `F = features(Market())`."""
import numpy as np
import lab


def features(mk):
    F = {'mk': mk}
    c, o, h, l, v = mk.c, mk.o, mk.h, mk.l, mk.v
    F['rsi'] = lab.rsi(c)
    F['atr'] = lab.atr_pct(h, l, c)
    F['ema20'], F['ema50'], F['ema200'] = lab.ema(c, 20), lab.ema(c, 50), lab.ema(c, 200)
    F['macd'], F['macds'], F['mup'], F['mdn'] = lab.macd(c)
    F['pctb'] = lab.pct_b(c)
    with np.errstate(divide='ignore', invalid='ignore'):
        F['volr'] = v / lab.roll(v, 20, np.mean)
    F['green'] = c >= o
    F['rsi_up'] = F['rsi'] >= np.roll(F['rsi'], 1, axis=1)

    # 4H, from bars that had closed
    o4, h4, l4, c4, v4 = mk.htf(4)
    e4 = lab.ema(c4, 50)
    F['c4'] = mk.to_1h(c4, 4)
    F['ema50_4h'] = mk.to_1h(e4, 4)
    F['ema50_4h_prev'] = mk.to_1h(np.roll(e4, 6, axis=1), 4)        # 24h earlier
    F['ema200_4h'] = mk.to_1h(lab.ema(c4, 200), 4)
    F['rsi4'] = mk.to_1h(lab.rsi(c4), 4)
    F['atr4'] = mk.to_1h(lab.atr_pct(h4, l4, c4), 4)

    # 1D
    o1, h1, l1, c1, v1 = mk.htf(24)
    F['c1d'] = mk.to_1h(c1, 24)
    F['ema20_1d'] = mk.to_1h(lab.ema(c1, 20), 24)
    F['ema50_1d'] = mk.to_1h(lab.ema(c1, 50), 24)
    F['hi20_1d'] = mk.to_1h(lab.roll(h1, 20, np.max), 24)
    F['ret7'] = c / np.roll(c, 168, axis=1) - 1
    F['ret30'] = c / np.roll(c, 720, axis=1) - 1
    F['ret7'][:, :168] = np.nan; F['ret30'][:, :720] = np.nan

    # BTC regime, as signal_checker.btc_regime_ok: bearish = 4H close under the 4H EMA50 and 4H RSI < 45
    b = mk.ix['BTCUSDT']
    F['bear'] = (F['c4'][b] < F['ema50_4h'][b]) & (F['rsi4'][b] < 45)
    F['btc_up_d'] = F['c1d'][b] > F['ema50_1d'][b]
    F['btc_ret7'] = F['ret7'][b]

    # breadth: share of listed coins that are oversold / above their 4H trend line
    listed = ~np.isnan(c)
    F['breadth_os'] = np.nansum((F['rsi'] <= 30) & listed, axis=0) / np.maximum(listed.sum(axis=0), 1)
    F['breadth_up'] = np.nansum((F['c4'] > F['ema50_4h']) & listed, axis=0) / np.maximum(listed.sum(axis=0), 1)

    # the live dip score (generate_signal), vectorised
    s = np.zeros_like(c)
    r = F['rsi']
    s += np.select([r <= 20, r <= 30, r >= 80, r >= 70, r >= 60], [3, 2, -3, -2, -1], 0)
    s += np.select([F['mup'], F['mdn'], F['macd'] > F['macds']], [2, -2, 0.5], -0.5)
    pb = F['pctb']
    s += np.select([pb <= 0.05, pb <= 0.20, pb >= 0.95, pb >= 0.80], [2, 1, -2, -1], 0)
    s += np.where(F['volr'] >= 2.0, 1, 0)
    r4 = F['rsi4']
    s += np.select([(s > 0) & (r4 <= 40), (s < 0) & (r4 >= 55), (s > 0) & (r4 >= 70), (s < 0) & (r4 <= 30)],
                   [1, -1, -0.5, 0.5], 0)
    s[np.isnan(r) | np.isnan(pb) | np.isnan(F['macds'])] = np.nan
    F['score'] = s
    return F
