"""Replay the live rules over the live period and line the replay's trades up against
the real ones: which setups both took, which only one took, and how each did."""
import calendar, json, os, sys, time
sys.stdout.reconfigure(encoding='utf-8')
import replay as R

HERE = os.path.dirname(os.path.abspath(__file__))
iso = lambda s: calendar.timegm(time.strptime(s[:16], '%Y-%m-%dT%H:%M')) * 1000
FROM = sys.argv[1] if len(sys.argv) > 1 else '2026-10-09 12:00'
coins = R.load('w_live')
T_FROM = R.utc(FROM)
ev = [e for c in coins.values() for e in R.events_for(c)]
t, blocks = R.portfolio(coins, ev, T_FROM, {'be_frac': 0.7})
live = [r for r in json.load(open(os.path.join(HERE, 'live_rows.json')))
        if r['created_at'][:16].replace('T', ' ') >= FROM]
match = lambda sym, T: [x for x in t if x['coin'] == sym and abs(x['T'] - T) <= 3 * R.H]
print(f'replay of the live rules from {FROM} UTC: {len(t)} trades; live bot: {len(live)} trades')
both = []
for r in live:
    m = match(r['symbol'], iso(r['created_at']))
    real = (r['net_pnl_usdt'] or 0) / r['amount_usdt'] * 100
    print(f"  LIVE {r['created_at'][:16]} {r['symbol']:<11} {r['exit_reason'] or 'open':<9} {real:+6.2f}%   | replay: "
          + (f"same setup, {m[0]['type']} {m[0]['frac'] * 100:+.2f}%" if m else 'did not take it'))
    if m:
        both.append(r)
for x in t:
    if not any(x['coin'] == r['symbol'] and abs(x['T'] - iso(r['created_at'])) <= 3 * R.H for r in live):
        print(f"  REPLAY ONLY {R.fmt(x['T'])} {x['coin']:<11} {x['type']:<9} {x['frac'] * 100:+6.2f}%")
s = R.stats(t)
lp = [(r['net_pnl_usdt'] or 0) / r['amount_usdt'] * 100 for r in live if r['phase'] == 3]
print(f"\nreplay: {s['n']} trades, {s['avg']:+.2f}% per trade, net {s['net']:+.2f} at $100 each")
print(f"live:   {len(lp)} closed, {sum(lp) / len(lp):+.2f}% per trade")
print('replay blocks:', dict(blocks))
