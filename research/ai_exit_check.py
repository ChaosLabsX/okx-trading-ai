"""
Did the AI's changes to the exit levels help? For every closed live trade that recorded
both the code's suggested exits and the AI's chosen ones, replay the trade on Binance
1-minute candles from its entry, twice: with the AI's exits and with the code's. The
first run is checked against what actually happened (validation); the difference
between the two is what the AI's exit tweaks were worth. Same entry, same size, same
break-even rule as was live at the time.
"""
import calendar, json, os, sys, time
import requests

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import signal_checker as sc

FEE = sc.OKX_FEE_RATE
BASE = 'https://data-api.binance.vision/api/v3/klines'
CACHE = os.path.join(HERE, 'w_trades')
os.makedirs(CACHE, exist_ok=True)
iso = lambda s: calendar.timegm(time.strptime(s[:19], '%Y-%m-%dT%H:%M:%S')) * 1000
BE_FROM, BE_FEES_FROM = iso('2026-10-09T12:00:00'), iso('2026-10-19T12:00:00')


def minutes(sym, start, end):
    fp = os.path.join(CACHE, f'{sym}_{start}_{end}.json')
    if os.path.exists(fp):
        return json.load(open(fp))
    out, t = [], start
    while t < end:
        for attempt in range(6):
            try:
                r = requests.get(BASE, params={'symbol': sym, 'interval': '1m', 'startTime': t,
                                               'endTime': end - 1, 'limit': 1000}, timeout=20)
                if r.status_code == 400:
                    return None                      # not listed on Binance
                r.raise_for_status(); b = r.json(); break
            except Exception:
                time.sleep(2 + attempt)
        else:
            return None
        if not b:
            break
        out += [[k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4])] for k in b]
        t = b[-1][0] + 60_000
    json.dump(out, open(fp, 'w'))
    return out


def sim(bars, e, tp, sl, trail, be_mode):
    """Option 3 on 1m bars from the entry: 50% at TP, trailing on the rest, BE as live."""
    nf = lambda px: (px - e) / e - FEE * (1 + px / e)
    sl_px, tp_px = e * (1 - sl / 100), e * (1 + tp / 100)
    be_trig = e * (1 + sc.BREAKEVEN_TRIGGER_FRAC * tp / 100) if be_mode else None
    be_px = sc._be_price(e) if be_mode == 'fees' else e
    phase, banked, peak, at_be = 1, 0.0, 0.0, False
    for ts, o, h, l, c in bars:
        if phase == 1:
            stop = be_px if at_be else sl_px
            if l <= stop:
                return ('be' if at_be else 'sl'), nf(min(o, stop)) * 100
            if h >= tp_px:
                phase, banked, peak = 2, nf(max(o, tp_px)) / 2, max(o, tp_px)
                continue
            if be_trig and not at_be and h >= be_trig:
                at_be = True
            continue
        floor = peak * (1 - trail / 100)
        if l <= floor:
            return 'tp_trail', (banked + nf(min(o, floor)) / 2) * 100
        peak = max(peak, h)
    return 'open', None


rows = json.load(open(os.path.join(HERE, 'live_rows.json')))
res = []
for r in rows:
    ec = r.get('entry_context') or {}
    s, c = ec.get('suggested') or {}, ec.get('chosen') or {}
    if r['phase'] != 3 or r.get('net_pnl_usdt') is None or not (s.get('tp') and c.get('tp_pct')):
        continue
    t0 = (iso(r['created_at']) // 60_000 + 1) * 60_000
    end = min(t0 + 14 * 86_400_000, int(time.time() * 1000) // 60_000 * 60_000)
    bars = minutes(r['symbol'].replace('-', ''), t0, end)
    if not bars:
        print(f"  {r['symbol']}: no Binance data — skipped"); continue
    e = float(r['entry_price'])
    be_mode = None if t0 < BE_FROM else ('entry' if t0 < BE_FEES_FROM else 'fees')
    ai = sim(bars, e, c['tp_pct'], c['sl_pct'], c['trail_pct'], be_mode)
    code = sim(bars, e, s['tp'], s['sl'], s['trail'], be_mode)
    actual = r['net_pnl_usdt'] / r['amount_usdt'] * 100
    res.append((r, actual, ai, code))
    print(f"  {r['created_at'][:10]} {r['symbol']:<11} actual {r['exit_reason']:<8} {actual:+6.2f}% | "
          f"AI exits {ai[0]:<8} {ai[1] if ai[1] is not None else float('nan'):+6.2f}% | "
          f"code exits {code[0]:<8} {code[1] if code[1] is not None else float('nan'):+6.2f}%   "
          f"(TP {c['tp_pct']} vs {s['tp']}, SL {c['sl_pct']} vs {s['sl']})")

ok = [x for x in res if x[2][1] is not None and x[3][1] is not None]
agree = sum(1 for r, a, ai, _ in ok if (a > 0) == (ai[1] > 0))
print(f'\nvalidation: replay of the AI exits matches the real outcome (win/loss) in {agree} of {len(ok)} trades; '
      f'mean real {sum(a for _, a, _, _ in ok) / len(ok):+.2f}% vs replayed {sum(ai[1] for _, _, ai, _ in ok) / len(ok):+.2f}%')
dai = sum(ai[1] for _, _, ai, _ in ok) / len(ok)
dcode = sum(cd[1] for _, _, _, cd in ok) / len(ok)
usd_ai = sum(ai[1] / 100 * r['amount_usdt'] for r, _, ai, _ in ok)
usd_code = sum(cd[1] / 100 * r['amount_usdt'] for r, _, _, cd in ok)
better = sum(1 for _, _, ai, cd in ok if cd[1] > ai[1] + 1e-9)
worse = sum(1 for _, _, ai, cd in ok if cd[1] < ai[1] - 1e-9)
print(f'per trade: AI exits {dai:+.2f}%  vs  code exits {dcode:+.2f}%   ({len(ok)} trades; code better in {better}, '
      f'AI better in {worse}, same in {len(ok) - better - worse})')
print(f'in dollars at the real sizes: AI exits {usd_ai:+.2f}  vs  code exits {usd_code:+.2f}')
for label, lo, hi in (('first half', 0, len(ok) // 2), ('second half', len(ok) // 2, len(ok))):
    part = ok[lo:hi]
    print(f'  {label} ({len(part)} trades): AI {sum(ai[1] for _, _, ai, _ in part) / len(part):+.2f}%  '
          f'code {sum(cd[1] for _, _, _, cd in part) / len(part):+.2f}%')
