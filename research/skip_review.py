"""
Were the AI's SKIP decisions right? Read-only summary of the skipped_setups ledger, run
on the VPS (the table needs the service key in C:\\OKXAI\\.env, which the public
dashboard key cannot read):

    cd C:\\OKXAI; .venv\\Scripts\\python.exe research\\skip_review.py

Each skip is graded 24h later by the worker: missed_win (the suggested take-profit was
hit before the stop - the skip cost money), good_skip (the stop would have been hit
first), neutral_skip (neither). Prints counts, the reasons the AI gave, and the same
for the trades it did take over the same period, so the two can be compared.
"""
import os, sys
from collections import Counter, defaultdict
import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from ai_check import load_env      # same .env rules as the worker's runner

load_env(os.path.join(ROOT, '.env'))
URL, KEY = os.environ.get('SUPABASE_URL', ''), os.environ.get('SUPABASE_KEY', '')
if not URL or not KEY:
    sys.exit('SUPABASE_URL / SUPABASE_KEY not found in .env')
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}


def get(table, select, extra=''):
    r = requests.get(f'{URL}/rest/v1/{table}?select={select}&order=created_at.asc{extra}', headers=H, timeout=30)
    r.raise_for_status()
    return r.json()


skips = get('skipped_setups', 'symbol,created_at,reason,followup,score:entry_context->score')
trades = get('option3_trades', 'symbol,created_at,net_pnl_usdt,amount_usdt,exit_reason', '&phase=eq.3')
if not skips:
    sys.exit('no skipped setups recorded')
since = skips[0]['created_at'][:10]
taken = [t for t in trades if t['created_at'] >= skips[0]['created_at'] and t.get('net_pnl_usdt') is not None]

verdict = lambda s: (s.get('followup') or {}).get('verdict') or 'not graded yet'
print(f'AI decisions since {since}: {len(taken)} TRADE, {len(skips)} SKIP '
      f'(skipped {len(skips) / (len(skips) + len(taken)) * 100:.0f}% of the setups it was asked about)')
print('\nSKIPS graded 24h later:', dict(Counter(verdict(s) for s in skips).most_common()))
wins = sum(1 for t in taken if t['net_pnl_usdt'] > 0)
print(f'TRADES taken over the same period: {wins} won / {len(taken) - wins} lost, '
      f'net {sum(t["net_pnl_usdt"] for t in taken):+.2f} USDT')

print('\nby month (skips: missed_win / good_skip / neutral):')
bym = defaultdict(Counter)
for s in skips:
    bym[s['created_at'][:7]][verdict(s)] += 1
for m, c in sorted(bym.items()):
    print(f"  {m}: {c.get('missed_win', 0)} / {c.get('good_skip', 0)} / {c.get('neutral_skip', 0)}"
          f"   (other {sum(c.values()) - c.get('missed_win', 0) - c.get('good_skip', 0) - c.get('neutral_skip', 0)})")

print('\nwhy it skipped (first words of the reason) -> verdicts:')
byr = defaultdict(Counter)
for s in skips:
    key = ' '.join((s.get('reason') or 'no reason').split()[:6])
    byr[key][verdict(s)] += 1
for k, c in sorted(byr.items(), key=lambda kv: -sum(kv[1].values()))[:15]:
    print(f'  {sum(c.values()):>3}x  {k[:60]:<60} {dict(c)}')

print('\nlast 15 skips:')
for s in skips[-15:]:
    print(f"  {s['created_at'][:16]} {s['symbol']:<11} score {s.get('score')}  {verdict(s):<15} {(s.get('reason') or '')[:70]}")
