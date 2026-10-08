"""
Minute-level replay of the LIVE entry rule (finished candles), built to compare exit,
sizing and frequency variants on equal terms. Uses the real functions from
signal_checker.py: generate_signal, reversal_confirmed, the volume gate, ATR and
support/resistance, suggest_exit_params, the downside-correlation guard, _rank_candidate.
Portfolio rules mirror run_scan: BTC regime filter, 4h per-coin suppression (applied
to every candidate), MAX_OPEN_TRADES, 3-SL/24h breaker, correlation guard, 1 trade
per 30m boundary. Exits are stepped on 1-minute candles: phase 1 = full position
with SL and a 50% take-profit (SL first when both fall in one minute), phase 2 =
trailing stop on the other half. The AI advisor is not modelled; $100 per trade.
"""
import array, bisect, calendar, json, os, sys, time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # repo root
import signal_checker as sc

HERE = os.path.dirname(os.path.abspath(__file__))
MIN, H = 60_000, 3_600_000
H30, H4 = 30 * MIN, 4 * H
FEE = 0.001
SUPPRESS = sc.REZONE_REMINDER * 1000

utc = lambda s: calendar.timegm(time.strptime(s, '%Y-%m-%d %H:%M')) * 1000
fmt = lambda ms: time.strftime('%Y-%m-%d %H:%M', time.gmtime(ms / 1000))

THIN = {f'{c}-USDT' for c in 'EGLD IMX THETA GRT PYTH COMP SAND AXS GALA MANA APE CHZ'.split()}
LIVE_UNIVERSE = [s for s in sc.SYMBOLS if s not in THIN]          # what the live bot scans today
WINDOWS = [('w4', '2025-10-21 00:00'), ('w3', '2026-01-19 00:00'),
           ('w2', '2026-04-19 00:00'), ('w_now', '2026-07-18 00:00')]  # eval starts after 8-day warm-up


class Coin:
    def __init__(self, inst, d):
        sym = inst.replace('-', '')
        self.inst = inst
        with open(os.path.join(d, f'{sym}.json')) as f:
            js = json.load(f)
        m = js['meta']
        self.w0, self.now, self.n = m['w_start'], m['now'], m['n']

        def closed(rows, step):
            rows = [r for r in rows if r[0] + step <= self.now]
            return [[r[k] for r in rows] for k in range(6)]
        self.t1, self.o1, self.h1, self.l1, self.c1, self.v1 = closed(js['1h'], H)
        self.t30, self.o30, _, _, self.c30, self.v30 = closed(js['30m'], H30)
        self.t4, _, _, _, self.c4, _ = closed(js['4h'], H4)
        self.m = {}
        for k in 'ohlc':
            a = array.array('d')
            with open(os.path.join(d, f'{sym}.1m.{k}'), 'rb') as f:
                a.frombytes(f.read())
            self.m[k] = a

    def mi(self, ts):
        return (ts - self.w0) // MIN

    def closed_1h(self, T):
        j = bisect.bisect_right(self.t1, T - H)
        if j < 100:
            return None
        return self.c1[j - 100:j], self.h1[j - 100:j], self.l1[j - 100:j], self.v1[j - 21:j]

    def closed_4h(self, T, count):
        j = bisect.bisect_right(self.t4, T - H4)
        return self.c4[j - count:j] if j >= count else None

    def closed_30m(self, T):
        j = bisect.bisect_right(self.t30, T - H30)
        return (self.o30[j - 50:j], self.c30[j - 50:j], self.v30[j - 50:j]) if j >= 50 else None


def load(window, universe=LIVE_UNIVERSE):
    d = os.path.join(HERE, window)
    insts = [x for x in universe if os.path.exists(os.path.join(d, x.replace('-', '') + '.json'))]
    if 'BTC-USDT' not in insts:
        insts.append('BTC-USDT')
    return {x: Coin(x, d) for x in insts}


def events_for(c, score_bar=None, minvol=None):
    """Every 30m boundary where the live rule would put this coin forward as a candidate.
    Stores ATR / support / resistance so exits can be recomputed per variant."""
    bar = sc.STRONG_BUY_SCORE if score_bar is None else score_bar
    mv = sc.MIN_VOL_RATIO_TRADE if minvol is None else minvol
    out = []
    for T in range((c.w0 // H30 + 1) * H30, c.now - MIN, H30):
        v = c.closed_1h(T)
        if v is None:
            continue
        closes, highs, lows, vols = v
        vr = sc.calc_vol_ratio(vols)
        if mv > 0 and (vr is None or vr < mv):
            continue
        r4c = c.closed_4h(T, 50)
        rev = c.closed_30m(T)
        if rev is None:
            continue
        rsi4 = sc.calc_rsi(r4c) if r4c else None
        rsi, macd, bb = sc.calc_rsi(closes), sc.calc_macd(closes), sc.calc_bb(closes)
        old = sc.STRONG_BUY_SCORE
        sc.STRONG_BUY_SCORE = bar
        try:
            sig = sc.generate_signal(rsi, macd, bb, vr, rsi4)
        finally:
            sc.STRONG_BUY_SCORE = old
        if sig['label'] != 'STRONG BUY' or not sc.reversal_confirmed(rev[0], rev[1], rev[2], 'up'):
            continue
        atr = sc.calc_atr_pct(highs, lows, closes)
        if not atr:
            continue
        sup, res = sc.find_support_resistance(highs, lows, closes)
        out.append({'T': T, 'coin': c.inst, 'entry_i': c.mi(T) + 1, 'score': sig['score'], 'vol': vr,
                    'rank': sc._rank_candidate(sig, rsi, rsi4, vr), 'atr': atr, 'sup': sup, 'res': res,
                    'px': closes[-1]})
    return out


def exits(e, tp_mult, sl_mult, tr_mult):
    old = sc.ATR_TP_MULT, sc.ATR_SL_MULT, sc.ATR_TRAIL_MULT
    sc.ATR_TP_MULT, sc.ATR_SL_MULT, sc.ATR_TRAIL_MULT = tp_mult, sl_mult, tr_mult
    try:
        return sc.suggest_exit_params(e['atr'], e['sup'], e['res'], e['px'])
    finally:
        sc.ATR_TP_MULT, sc.ATR_SL_MULT, sc.ATR_TRAIL_MULT = old


def simulate(c, entry_i, ex, be_atr=None, atr=None, time_stop_h=None, be_frac=None):
    """Returns (exit_i, type, pnl_fraction_of_stake). Break-even trigger either at
    +be_atr x ATR above entry, or at be_frac of the way to the take-profit."""
    mo, mh, ml, mc = c.m['o'], c.m['h'], c.m['l'], c.m['c']
    if entry_i >= c.n:
        return None
    e = mo[entry_i]
    nf = lambda px: (px - e) / e - FEE * (1 + px / e)
    sl_px, tp_px = e * (1 - ex['sl'] / 100), e * (1 + ex['tp'] / 100)
    be_px = (e * (1 + be_atr * atr / 100) if be_atr else
             e * (1 + be_frac * ex['tp'] / 100) if be_frac else None)
    stop_deadline = entry_i + int(time_stop_h * 60) if time_stop_h else None
    phase, banked, peak, at_be = 1, 0.0, 0.0, False
    for i in range(entry_i, c.n):
        if phase == 1:
            stop = e if at_be else sl_px
            if ml[i] <= stop:
                fill = mo[i] if mo[i] <= stop else stop
                return i, ('be' if at_be else 'sl'), nf(fill)
            if mh[i] >= tp_px:
                fill = mo[i] if mo[i] >= tp_px else tp_px
                phase, banked, peak = 2, nf(fill) / 2, fill
                continue
            if be_px and not at_be and mh[i] >= be_px:
                at_be = True                  # takes effect from the next minute
            if stop_deadline and i >= stop_deadline:
                return i, 'time', nf(mc[i])
            continue
        floor = peak * (1 - ex['trail'] / 100)
        if ml[i] <= floor:
            fill = mo[i] if mo[i] <= floor else floor
            return i, 'tp_trail', banked + nf(fill) / 2
        peak = max(peak, mh[i])
    last = mc[c.n - 1]
    return c.n - 1, 'open', (nf(last) if phase == 1 else banked + nf(last) / 2)


def btc_bear(btc, T, cache):
    if T not in cache:
        b = btc.closed_4h(T, 100)
        if not b or len(b) < 60:
            cache[T] = False
        else:
            r = sc.calc_rsi(b)
            cache[T] = b[-1] < sc.ema_array(b, 50)[-1] and r is not None and r < 45
    return cache[T]


def portfolio(coins, events, t_from, v):
    """v: variant dict - tp, sl, tr (ATR multiples), be_atr, time_stop_h, max_open, per_scan."""
    btc, bear_cache = coins['BTC-USDT'], {}
    groups = defaultdict(list)
    for e in events:
        if e['T'] >= t_from:
            groups[e['T']].append(e)
    last, open_tr, trades, sl_times, blocks = {}, {}, [], [], Counter()
    max_open = v.get('max_open', sc.MAX_OPEN_TRADES)
    for T in sorted(groups):
        for coin in list(open_tr):
            if open_tr[coin]['exit_T'] <= T:
                if open_tr[coin]['type'] == 'sl':
                    sl_times.append(open_tr[coin]['exit_T'])
                del open_tr[coin]
        cands = []
        for e in groups[T]:
            if T - last.get(e['coin'], -10**15) < SUPPRESS:
                continue
            last[e['coin']] = T
            if e['coin'] in open_tr:
                blocks['already_open'] += 1; continue
            cands.append(e)
        if not cands:
            continue
        if btc_bear(btc, T, bear_cache):
            blocks['btc_regime'] += len(cands); continue
        if len(open_tr) >= max_open:
            blocks['max_open'] += len(cands); continue
        if sum(1 for s in sl_times if T - 24 * H < s <= T) >= sc.MAX_SL_PER_DAY:
            blocks['sl_breaker'] += len(cands); continue
        if open_tr:
            kept = []
            for e in cands:
                mine = coins[e['coin']].closed_1h(T)
                bad = False
                for oc in open_tr:
                    theirs = coins[oc].closed_1h(T)
                    if mine and theirs:
                        r = sc._downside_corr(mine[0], theirs[0])
                        if r is not None and r >= sc.CORRELATION_MAX:
                            bad = True; break
                if bad:
                    blocks['correlation'] += 1
                else:
                    kept.append(e)
            cands = kept
        cands.sort(key=lambda e: e['rank'], reverse=True)
        for e in cands[:min(v.get('per_scan', 1), max_open - len(open_tr))]:
            ex = exits(e, v.get('tp', sc.ATR_TP_MULT), v.get('sl', sc.ATR_SL_MULT), v.get('tr', sc.ATR_TRAIL_MULT))
            if not ex:
                continue
            c = coins[e['coin']]
            out = simulate(c, e['entry_i'], ex, v.get('be_atr'), e['atr'], v.get('time_stop_h'), v.get('be_frac'))
            if out is None:
                continue
            exit_i, typ, frac = out
            tr = {'T': T, 'coin': e['coin'], 'type': typ, 'frac': frac, 'sl': ex['sl'], 'tp': ex['tp'],
                  'trail': ex['trail'], 'score': e['score'], 'exit_T': c.w0 + (exit_i + 1) * MIN}
            open_tr[e['coin']] = tr
            trades.append(tr)
    return trades, blocks


def stats(trades, weight=None):
    """weight(trade) -> stake multiplier; default $100 flat."""
    w = (lambda t: 1.0) if weight is None else weight
    pnl = [100 * w(t) * t['frac'] for t in trades]
    n = len(pnl)
    win = sum(p for p in pnl if p > 0); loss = -sum(p for p in pnl if p <= 0)
    cum = peak = dd = 0.0
    for t, p in sorted(zip(trades, pnl), key=lambda z: z[0]['T']):
        cum += p; peak = max(peak, cum); dd = max(dd, peak - cum)
    return {'n': n, 'net': sum(pnl), 'avg': sum(pnl) / n if n else 0.0,
            'pf': win / loss if loss else float('inf'), 'dd': dd,
            'wr': sum(1 for p in pnl if p > 0) / n if n else 0.0}
