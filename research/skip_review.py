"""
Was saying no the right call? Read-only summary of the skipped_setups journal, run on
the VPS:

    cd C:\\OKXAI; .venv\\Scripts\\python.exe research\\skip_review.py

Every qualified signal that was not traded is in that table with the gate that refused it
(ai, regime, max_open, breaker, loss_limit, correlation, cap, funding). Each is graded 24h
later by the worker: good_skip (the stop would have been hit first - the "no" saved
money), missed_win (the target would have been hit first - it cost a win), neutral_skip
(neither). Prints the tally per gate, then the AI's own skips in detail beside the trades
it did take (live and paper) over the same period.

The dashboard's "Why no trade?" tab shows the same record; this is the version to paste
into a chat.
"""
import os, sys
from collections import Counter, defaultdict
import requests

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from ai_check import load_env      # same .env rules as the worker's runner

load_env(os.path.join(ROOT, '.env'))
import signal_checker as sc        # after load_env: it reads the environment at import
URL, KEY = sc.SUPABASE_URL, sc.SUPABASE_KEY
if not URL or not KEY:
    sys.exit('SUPABASE_URL / SUPABASE_KEY not found in .env')


def get(table, select, extra='', order='created_at'):
    r = requests.get(f'{URL}/rest/v1/{table}?select={select}&order={order}.asc{extra}',
                     headers=sc._sb_headers(), timeout=30)
    if r.status_code in (401, 403):
        sys.exit(f'{table}: HTTP {r.status_code} - the key cannot read it. Run db_check.py.')
    if r.status_code == 400 and 'gate' in select:
        sys.exit('the `gate` column is missing - run infra/supabase-3-paper-and-status.sql')
    r.raise_for_status()
    return r.json()


rows = get('skipped_setups', 'symbol,created_at,reason,gate,followup,score:entry_context->score')
if not rows:
    sys.exit('no refused signals recorded yet')
verdict = lambda s: (s.get('followup') or {}).get('verdict') or 'not graded yet'
NAMES = {'ai': 'AI advisor', 'regime': 'BTC downtrend filter', 'max_open': 'open-trade cap',
         'breaker': 'stop-loss breaker', 'loss_limit': 'monthly loss limit', 'correlation': 'correlation guard',
         'cap': 'ranked below top pick', 'funding': 'crowded funding'}

print(f'{len(rows)} qualified signals were not traded since {rows[0]["created_at"][:10]}.\n')
print(f"{'who said no':<24}{'refused':>8}{'checked':>9}{'would have LOST':>17}{'would have WON':>16}{'neither':>9}")
by_gate = defaultdict(Counter)
for s in rows:
    by_gate[s.get('gate') or 'ai'][verdict(s)] += 1
for g, c in sorted(by_gate.items(), key=lambda kv: -sum(kv[1].values())):
    n = sum(c.values())
    graded = n - c.get('not graded yet', 0) - c.get('ungradable', 0)
    print(f"{NAMES.get(g, g):<24}{n:>8}{graded:>9}{c.get('good_skip', 0):>17}{c.get('missed_win', 0):>16}{c.get('neutral_skip', 0):>9}")

skips = [s for s in rows if (s.get('gate') or 'ai') == 'ai']
if not skips:
    sys.exit('\nthe AI itself has not skipped anything yet (every refusal above came from a safety rail)')
start = skips[0]['created_at']
live = [t for t in get('option3_trades', 'symbol,created_at,net_pnl_usdt', '&phase=eq.3')
        if t['created_at'] >= start and t.get('net_pnl_usdt') is not None]
paper_ = [t for t in get('paper_trades', 'symbol,opened_at,net_pnl_usdt', '&strategy=eq.dip&closed_at=not.is.null', 'opened_at')
          if t['opened_at'] >= start]
taken = live + paper_
print(f'\nAI decisions since {start[:10]}: {len(taken)} TRADE ({len(live)} live, {len(paper_)} paper), {len(skips)} SKIP '
      f'(skipped {len(skips) / (len(skips) + len(taken)) * 100:.0f}% of the setups it was asked about)')
print('AI skips graded 24h later:', dict(Counter(verdict(s) for s in skips).most_common()))
wins = sum(1 for t in taken if t['net_pnl_usdt'] > 0)
print(f'Trades it took over the same period: {wins} won / {len(taken) - wins} lost, '
      f'net {sum(t["net_pnl_usdt"] for t in taken):+.2f} USDT')

print('\nwhy the AI skipped (first words of the reason) -> verdicts:')
byr = defaultdict(Counter)
for s in skips:
    byr[' '.join((s.get('reason') or 'no reason').split()[:6])][verdict(s)] += 1
for k, c in sorted(byr.items(), key=lambda kv: -sum(kv[1].values()))[:15]:
    print(f'  {sum(c.values()):>3}x  {k[:60]:<60} {dict(c)}')

print('\nlast 20 refusals (all gates):')
for s in rows[-20:]:
    print(f"  {s['created_at'][:16]} {s['symbol']:<11} {NAMES.get(s.get('gate') or 'ai', s.get('gate')):<22} "
          f"{verdict(s):<15} {(s.get('reason') or '')[:60]}")
