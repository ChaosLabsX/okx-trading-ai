"""Lab experiment 1: does any simple long-only entry family have an edge on hourly bars,
2024-01 -> 2026-11, ~77 coins, after 0.30% round-trip costs?  Families, pre-specified:

  DIP   the live rule, approximated on hourly bars (30m reversal -> hourly reversal)
  TPB   trend pullback: 4H uptrend, hourly RSI dipped, price closes back above its EMA20
  BRK   breakout: close above the prior N-hour high on 2x volume, in a 4H uptrend
  BRKD  daily breakout: close above the prior 20-day high, in a 4H uptrend
  MACDX momentum turn: hourly MACD bullish cross below zero, in a 4H uptrend

Each is shown as SETUPS (every signal traded alone) and BOOK (max 3 open, 1 new per hour),
with the live exits and two wider "trend" exit sets."""
import sys, time
import numpy as np
import lab
from lab_feat import features

sys.stdout.reconfigure(encoding='utf-8')
t0 = time.time()
mk = lab.Market()
F = features(mk)
print(f'{len(mk.syms)} coins, {mk.n} hourly bars, {lab.fmt(mk.t[0])} -> {lab.fmt(mk.t[-1])}  '
      f'(features {time.time() - t0:.0f}s)')
i0 = 24 * 45                                     # warm-up for the slow averages

ok = ~F['bear'][None, :]
up4 = (F['c4'] > F['ema50_4h']) & (F['ema50_4h'] > F['ema50_4h_prev'])
c, rsi_, volr = mk.c, F['rsi'], F['volr']
prev = lambda x, k=1: np.roll(x, k, axis=1)

rules = {}
rules['DIP  live rule (approx)'] = ((F['score'] >= 4.0) & (volr >= 2.0) & F['green'] & F['rsi_up']
                                    & (mk.v >= lab.roll(mk.v, 20, np.mean)) & ok)
rules['DIP  same, any regime'] = ((F['score'] >= 4.0) & (volr >= 2.0) & F['green'] & F['rsi_up']
                                  & (mk.v >= lab.roll(mk.v, 20, np.mean)))
dipped = lab.roll_incl(rsi_, 6, np.min) <= 40
cross20 = (c > F['ema20']) & (prev(c) <= prev(F['ema20'])) & F['green']
rules['TPB  pullback + EMA20 reclaim'] = up4 & dipped & cross20 & ok
rules['TPB  + volume 1.5x'] = up4 & dipped & cross20 & ok & (volr >= 1.5)
for N in (24, 48, 96):
    hh = lab.roll(mk.h, N, np.max)
    rules[f'BRK  {N}h high, vol 2x'] = (c > hh) & (volr >= 2.0) & up4 & ok & F['green']
rules['BRK  48h high, no vol rule'] = (c > lab.roll(mk.h, 48, np.max)) & up4 & ok & F['green']
rules['BRKD 20-day high'] = (c > F['hi20_1d']) & (prev(c) <= prev(F['hi20_1d'])) & up4 & ok
rules['MACDX cross below zero, 4H up'] = F['mup'] & (F['macd'] < 0) & up4 & ok
rules['MACDX cross, any trend'] = F['mup'] & (F['macd'] < 0) & ok

EXITS = {'live 2.0/2.5/1.0': dict(tp=2.0, sl=2.5, tr=1.0),
         'trend 3.0/2.0/2.0': dict(tp=3.0, sl=2.0, tr=2.0),
         'wide 4.0/2.5/3.0': dict(tp=4.0, sl=2.5, tr=3.0)}

for xname, X in EXITS.items():
    print(f'\n=== exits {xname} (TP x ATR on half / SL x ATR / trail x ATR) ===')
    for name, sig in rules.items():
        setups, book = lab.run(mk, sig, F['atr'], X, rank=F['volr'], i_from=i0)
        print(lab.line('S ' + name, lab.stats(mk, setups)))
        print(lab.line('B ' + name, lab.stats(mk, book)))
print(f'\n{time.time() - t0:.0f}s')
