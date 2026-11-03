"""
Live book review (read-only, public anon key from config.js): overall, by period, by
score bucket, by exit type, AI exit choices vs the code's suggestion, sizing.
Writes the rows it fetched to research/live_rows.json for the other review scripts.
"""
import json, os, statistics as st, sys
from collections import Counter, defaultdict
import requests

sys.stdout.reconfigure(encoding='utf-8')
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cfg = open(os.path.join(REPO, 'config.js'), encoding='utf-8').read()
KEY = cfg.split("SUPABASE_ANON_KEY: '")[1].split("'")[0]
H = {'apikey': KEY, 'Authorization': 'Bearer ' + KEY}
COLS = ('id,symbol,created_at,closed_at,phase,amount_usdt,entry_price,exit_price,net_pnl_usdt,'
        'exit_reason,partial_tp_pct,sl_pct,trailing_pct,followup,entry_context')
rows = requests.get('https://trbfhtopkcupzeqmrnom.supabase.co/rest/v1/option3_trades'
                    f'?select={COLS}&order=created_at.asc', headers=H, timeout=30).json()
json.dump(rows, open(os.path.join(REPO, 'research', 'live_rows.json'), 'w'))

closed = [r for r in rows if r['phase'] == 3 and r.get('net_pnl_usdt') is not None]
openr = [r for r in rows if r['phase'] != 3]
print(f'{len(rows)} trades, {len(closed)} closed with P&L, open: '
      + (', '.join(f"{r['symbol']} {r['created_at'][:10]} ${r['amount_usdt']}" for r in openr) or 'none'))
pct = lambda r: r['net_pnl_usdt'] / r['amount_usdt'] * 100


def line(label, rs):
    if not rs:
        print(f'{label:<38} n= 0'); return
    p = [r['net_pnl_usdt'] for r in rs]; q = [pct(r) for r in rs]
    w = [x for x in p if x > 0]; l = [x for x in p if x <= 0]
    pf = sum(w) / -sum(l) if l and sum(l) < 0 else float('inf')
    wq = [x for x in q if x > 0]; lq = [x for x in q if x <= 0]
    print(f'{label:<38} n={len(rs):>2} {len(w):>2}W/{len(l):<2}L net {sum(p):+7.2f}  PF {pf:5.2f}  '
          f'avg/trade {st.mean(q):+.2f}%  win {st.mean(wq) if wq else 0:+.2f}%  loss {st.mean(lq) if lq else 0:+.2f}%  '
          f'median ${st.median(r["amount_usdt"] for r in rs):.0f}')


line('ALL', closed)
line('to Sep 3 (small sizes)', [r for r in closed if r['created_at'] < '2026-09-03'])
line('Sep 3 - Sep 21 (45-60%, 38 coins)', [r for r in closed if '2026-09-03' <= r['created_at'] < '2026-09-21'])
line('Sep 21 - Oct 9 (67 coins)', [r for r in closed if '2026-09-21' <= r['created_at'] < '2026-10-09T12'])
line('since Oct 9 (4.0 bar + break-even)', [r for r in closed if r['created_at'] >= '2026-10-09T12'])
line('since Opus 5.5 (Sep 28 12:00)', [r for r in closed if r['created_at'] >= '2026-09-28T12'])
line('last 30 (drives the size cap)', closed[-30:])

print('\nby entry score:')
for lo, hi, lab in ((0, 4.5, '4.0'), (4.5, 5.0, '4.5'), (5.0, 6.0, '5.0-5.5'), (6.0, 99, '6.0+')):
    line(f'  score {lab}', [r for r in closed if lo <= float((r.get('entry_context') or {}).get('score') or 0) < hi])

print('\nby exit:')
for k, n in Counter(r['exit_reason'] for r in closed).most_common():
    line(f'  {k}', [r for r in closed if r['exit_reason'] == k])

print('\nverdicts on losing trades (graded 24h after the exit):',
      dict(Counter((r.get('followup') or {}).get('verdict') for r in closed if r['net_pnl_usdt'] <= 0)))
print('verdicts on winning trades:',
      dict(Counter((r.get('followup') or {}).get('verdict') for r in closed if r['net_pnl_usdt'] > 0)))

print('\nAI exits vs the code suggestion (trades with both recorded):')
d = defaultdict(list)
for r in closed:
    ec = r.get('entry_context') or {}
    s, c = ec.get('suggested') or {}, ec.get('chosen') or {}
    if s.get('tp') and c.get('tp_pct') and s.get('sl') and c.get('sl_pct'):
        d['tp'].append(c['tp_pct'] / s['tp'] - 1); d['sl'].append(c['sl_pct'] / s['sl'] - 1)
        d['rr_s'].append(s['tp'] / s['sl']); d['rr_c'].append(c['tp_pct'] / c['sl_pct'])
print(f"  n={len(d['tp'])}  median TP change {st.median(d['tp']) * 100:+.0f}%, SL change {st.median(d['sl']) * 100:+.0f}%;"
      f"  target/stop ratio: code {st.median(d['rr_s']):.2f}, AI {st.median(d['rr_c']):.2f}")

print('\nper coin (closed trades, net):')
pc = defaultdict(list)
for r in closed:
    pc[r['symbol'].replace('-USDT', '')].append(r['net_pnl_usdt'])
print('  ' + ', '.join(f'{k} {sum(v):+.2f} ({len(v)})' for k, v in sorted(pc.items(), key=lambda kv: sum(kv[1]))))

print('\nsince Oct 9, trade by trade:')
for r in [r for r in rows if r['created_at'] >= '2026-10-09T12']:
    ec = r.get('entry_context') or {}
    print(f"  {r['created_at'][:16]} {r['symbol']:<11} score {ec.get('score')} ${r['amount_usdt']:<6} "
          f"TP {r['partial_tp_pct']} SL {(ec.get('chosen') or {}).get('sl_pct')} -> {r['exit_reason'] or 'open':<9} "
          f"{(r['net_pnl_usdt'] or 0):+.2f}  {(r.get('followup') or {}).get('verdict') or ''}")
