"""Live record from the trade database (read-only, public anon key from config.js)."""
import json, os, sys, statistics as st
from collections import Counter
import requests
sys.stdout.reconfigure(encoding='utf-8')
cfg = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.js'),
           encoding='utf-8').read()
KEY = cfg.split("SUPABASE_ANON_KEY: '")[1].split("'")[0]
H = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY}
rows = requests.get('https://trbfhtopkcupzeqmrnom.supabase.co/rest/v1/option3_trades'
                    '?select=*&order=created_at.asc', headers=H, timeout=30).json()
closed = [r for r in rows if r['phase'] == 3 and r.get('net_pnl_usdt') is not None]
openr = [r for r in rows if r['phase'] != 3]
print(f'{len(rows)} trades total, {len(closed)} closed, {len(openr)} open: '
      + ', '.join(f"{r['symbol']} {r['created_at'][:10]} ${r['amount_usdt']}" for r in openr))


def summary(label, rs):
    if not rs:
        return
    p = [r['net_pnl_usdt'] for r in rs]
    pct = [r['net_pnl_usdt'] / r['amount_usdt'] * 100 for r in rs]
    w = [x for x in p if x > 0]; l = [x for x in p if x <= 0]
    wp = [x for x in pct if x > 0]; lp = [x for x in pct if x <= 0]
    pf = sum(w) / -sum(l) if l else float('inf')
    print(f'{label:<34} n={len(rs):>2} {len(w)}W/{len(l)}L  net {sum(p):+7.2f}  PF {pf:4.2f}  '
          f'avg win {st.mean(wp) if wp else 0:+.2f}%  avg loss {st.mean(lp) if lp else 0:+.2f}%  '
          f'avg/trade {st.mean(pct):+.2f}%  median size ${st.median(r["amount_usdt"] for r in rs):.0f}')


summary('all closed', closed)
summary('before 45-60% sizing (<= Sep 3)', [r for r in closed if r['created_at'] < '2026-09-03'])
summary('after sizing (> Sep 3)', [r for r in closed if r['created_at'] >= '2026-09-03'])
summary('since 67 coins (>= Sep 21)', [r for r in closed if r['created_at'] >= '2026-09-21'])
summary('since Opus 5.5 (>= Sep 28 12:00)', [r for r in closed if r['created_at'] >= '2026-09-28T12'])
summary('last 30 (drives the size cap)', closed[-30:])

print('\nexit verdicts (graded 24h after exit):', dict(Counter((r.get('followup') or {}).get('verdict') for r in closed)))
wins = [r for r in closed if r['net_pnl_usdt'] > 0]
print('winners whose verdict is left_money:', sum(1 for r in wins if (r.get('followup') or {}).get('verdict') == 'left_money'),
      'of', sum(1 for r in wins if (r.get('followup') or {}).get('verdict')), 'graded winners')
lm = [r['followup'] for r in wins if (r.get('followup') or {}).get('verdict') == 'left_money']
if lm:
    print('  how far price ran past the exit (peak % in next 24h):', sorted(round(f['peak_pct'], 1) for f in lm))
print('exit reasons:', dict(Counter(r['exit_reason'] for r in closed)))

print('\nAI exits vs suggested (where recorded):')
dtp, dsl, dtr = [], [], []
for r in closed + openr:
    ec = r.get('entry_context') or {}
    s, c = ec.get('suggested') or {}, ec.get('chosen') or {}
    if s.get('tp') and c.get('tp_pct'):
        dtp.append(c['tp_pct'] / s['tp'] - 1); dsl.append(c['sl_pct'] / s['sl'] - 1)
        if s.get('trail') and c.get('trail_pct'):
            dtr.append(c['trail_pct'] / s['trail'] - 1)
print(f'  n={len(dtp)}  median change: TP {st.median(dtp) * 100:+.0f}%  SL {st.median(dsl) * 100:+.0f}%  trail {st.median(dtr) * 100:+.0f}%')
tp_lt_sl = sum(1 for r in closed if r['partial_tp_pct'] < r['sl_pct'])
print(f'  trades where the stop was wider than the target: {tp_lt_sl} of {len(closed)}')
