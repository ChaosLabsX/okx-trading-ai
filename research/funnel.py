"""Why did the bot not trade? Walk the live entry rule gate by gate over a window and count
how many coin-moments survive each one, in the order run_scan applies them:

    scored  ->  STRONG BUY (score >= bar)  ->  30m reversal  ->  1H volume >= 2x
            ->  not suppressed (one candidate per coin per 4h)  ->  BTC regime OK  ->  reaches the AI

usage: funnel.py WINDOW "YYYY-MM-DD HH:MM" ["YYYY-MM-DD HH:MM"]     (UTC from, optional to)
"""
import os, sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import replay as rp
sc = rp.sc

sys.stdout.reconfigure(encoding='utf-8')
window, t_from = sys.argv[1], rp.utc(sys.argv[2])
t_to = rp.utc(sys.argv[3]) if len(sys.argv) > 3 else None

coins = rp.load(window)
btc, bear_cache = coins['BTC-USDT'], {}
end = min(c.now for c in coins.values())
if t_to:
    end = min(end, t_to)

stage = Counter()
by_score = Counter()
last = {}
events, blocked_regime = [], []
bear_n = tot_n = 0
day_stats = defaultdict(lambda: Counter())

for T in range((t_from // rp.H30 + 1) * rp.H30, end, rp.H30):
    bear = rp.btc_bear(btc, T, bear_cache)
    tot_n += 1; bear_n += bear
    day = rp.fmt(T)[:10]
    day_stats[day]['bear'] += bear; day_stats[day]['n'] += 1
    for inst, c in coins.items():
        v = c.closed_1h(T)
        rev = c.closed_30m(T)
        if v is None or rev is None:
            continue
        closes, highs, lows, vols = v
        vr = sc.calc_vol_ratio(vols)
        r4c = c.closed_4h(T, 50)
        rsi4 = sc.calc_rsi(r4c) if r4c else None
        rsi, macd, bb = sc.calc_rsi(closes), sc.calc_macd(closes), sc.calc_bb(closes)
        sig = sc.generate_signal(rsi, macd, bb, vr, rsi4)
        stage['1 scored'] += 1
        s = sig['score']
        if s >= 2.0:
            by_score[min(s, 7.0)] += 1
        if sig['label'] != 'STRONG BUY':
            continue
        stage['2 STRONG BUY (score >= %g)' % sc.STRONG_BUY_SCORE] += 1
        day_stats[day]['sb'] += 1
        rv = sc.reversal_confirmed(rev[0], rev[1], rev[2], 'up')
        vol_ok = vr is not None and vr >= sc.MIN_VOL_RATIO_TRADE
        if not rv:
            stage['  x no 30m reversal'] += 1
            if vol_ok:
                stage['    (volume was fine)'] += 1
            continue
        stage['3 + 30m reversal'] += 1
        if not vol_ok:
            stage['  x 1H volume under %gx' % sc.MIN_VOL_RATIO_TRADE] += 1
            continue
        stage['4 + volume >= %gx' % sc.MIN_VOL_RATIO_TRADE] += 1
        if T - last.get(inst, -10**15) < rp.SUPPRESS:
            stage['  x same coin within 4h'] += 1
            continue
        last[inst] = T
        stage['5 distinct candidates'] += 1
        day_stats[day]['cand'] += 1
        e = {'T': T, 'coin': inst, 'score': s, 'vr': vr, 'rsi': rsi, 'rsi4': rsi4,
             'cross': bool(macd and macd['bullish_cross']), 'px': closes[-1]}
        if bear:
            stage['  x BTC regime bearish'] += 1
            blocked_regime.append(e)
            continue
        stage['6 reach the AI'] += 1
        day_stats[day]['ai'] += 1
        events.append(e)

print(f'window {rp.fmt(t_from)} -> {rp.fmt(end)} UTC, {len(coins)} coins, '
      f'{tot_n} half-hour checks')
print(f'BTC regime bearish for {bear_n} of {tot_n} checks ({bear_n / tot_n:.0%})\n')
for k in sorted(stage, key=lambda k: (k.strip()[0].isdigit() and k.strip()[0] or '9', k)):
    pass
order = ['1 scored', '2 STRONG BUY (score >= %g)' % sc.STRONG_BUY_SCORE, '  x no 30m reversal',
         '    (volume was fine)', '3 + 30m reversal', '  x 1H volume under %gx' % sc.MIN_VOL_RATIO_TRADE,
         '4 + volume >= %gx' % sc.MIN_VOL_RATIO_TRADE, '  x same coin within 4h', '5 distinct candidates',
         '  x BTC regime bearish', '6 reach the AI']
for k in order:
    print(f'{k:<34} {stage.get(k, 0):>8}')

print('\nscore distribution of coin-moments at score >= 2 (half-hour checks):')
for s in sorted(by_score):
    print(f'  score {s:>4}{"+" if s >= 7 else " "} {by_score[s]:>6}')

print('\nper day: bearish share of the day | STRONG BUY moments | distinct candidates | reach the AI')
for d in sorted(day_stats):
    x = day_stats[d]
    print(f'  {d}  bear {x["bear"] / x["n"]:>4.0%}   sb {x["sb"]:>4}   cand {x["cand"]:>3}   ai {x["ai"]:>3}')

print(f'\ncandidates that reached the AI ({len(events)}):')
for e in events:
    print(f'  {rp.fmt(e["T"])}  {e["coin"]:<11} score {e["score"]:.1f}  vol {e["vr"]:.1f}x  '
          f'rsi {e["rsi"]:.0f}  rsi4h {e["rsi4"]:.0f}  {"MACD cross" if e["cross"] else ""}')
print(f'\ncandidates blocked by the BTC regime ({len(blocked_regime)}):')
for e in blocked_regime[:60]:
    print(f'  {rp.fmt(e["T"])}  {e["coin"]:<11} score {e["score"]:.1f}  vol {e["vr"]:.1f}x  '
          f'rsi {e["rsi"]:.0f}  rsi4h {e["rsi4"]:.0f}  {"MACD cross" if e["cross"] else ""}')
