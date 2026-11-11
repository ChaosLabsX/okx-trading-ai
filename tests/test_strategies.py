"""
The strategy ladder (strategies.py): who trades real money and how much, decided from each
strategy's own record - and the routing of flush / trend signals to a real order or a
paper trade. No network: the database, OKX orders and Telegram are stubs.

    python tests/test_strategies.py
"""
import contextlib, io, math, os, sys
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import signal_checker as sc
import paper
import strategies as S

N = 0


def ok(cond, what):
    global N
    assert cond, 'FAILED: ' + what
    N += 1
    print('ok   ' + what)


TG = []
sc.send_telegram = lambda m: TG.append(m)
sc.time.sleep = lambda s: None
S.time.sleep = lambda s: None


def T(pct, day, live=False, usd=None, closed=None):
    return {'day': day, 'pct': pct, 'live': live, 'usd': pct if usd is None else usd,
            'closed': closed or datetime.now(timezone.utc).isoformat()}


# ── 1. statistics: a market-wide crash day is ONE observation ─────────────────
same_day = [T(5.0, '2026-11-12') for _ in range(20)] + [T(-1.0, f'2026-11-{d:02d}') for d in range(13, 23)]
st = S.stats(same_day)
ok(st['n'] == 30 and st['days'] == 11 and abs(st['mean'] - 3.0) < 1e-9,
   '30 trades, 20 of them on one crash day: 30 trades but 11 independent days')
ok(st['se'] is not None and st['day_mean'] < 0 and st['day_mean'] - S.SE_MULT * st['se'] < 0,
   f"per trade +3.00%, but per day {st['day_mean']:+.2f}%: twenty same-day winners do NOT pass as \"not luck\"")
ok(S.decide('x', st, 300.0, mode='auto', start='paper')[0] == 'paper',
   'so a strategy whose wins all came on one crash day is NOT promoted')
spread = [T(3.0 if i % 3 else -1.5, f'2026-{11 + i // 28}-{1 + i % 28:02d}') for i in range(36)]
st2 = S.stats(spread)
ok(st2['n'] == 36 and st2['days'] == 36 and st2['mean'] - S.SE_MULT * st2['se'] > 0 and st2['pf'] >= S.MIN_PF,
   f"36 trades on 36 days, +{st2['mean']:.2f}% average: clears the bar")
ok(S.stats([])['pf'] is None and S.stats([T(1, 'a'), T(2, 'b')])['pf'] == math.inf, 'no losses: profit factor infinite, none: undefined')

# ── 2. the ladder ─────────────────────────────────────────────────────────────
E = 300.0


def D(st, start='paper', mode='auto'):
    return S.decide('x', st, E, mode=mode, start=start)[0]


empty = S.stats([])
ok(D(empty, 'trial') == 'trial' and D(empty, 'paper') == 'paper', 'no record: each strategy stands on its start rung')
ok(D(st2, 'paper') == 'trial', 'paper strategy whose record passes the bar: promoted to a real-money TRIAL, not straight to full')
live10 = [dict(t, live=(i < 10)) for i, t in enumerate(spread)]
ok(D(S.stats(live10), 'trial') == 'full', 'passes with 10+ real trades in its record: FULL size')
ok(D(S.stats(spread[:12]), 'trial') == 'trial', '12 trades, however good: stays on trial (needs 30)')
losers = [T(-0.5 if i % 2 else 0.2, f'2026-11-{1 + i % 28:02d}') for i in range(30)]
lvl, why = S.decide('x', S.stats(losers), E, mode='auto', start='trial')
ok(lvl == 'paper' and 'lost money on average' in why, '30 trades that lose on average: back to PAPER, from any rung')
burn = [T(-25.0, '2026-11-12', live=True, usd=-5.0) for _ in range(4)]           # $20 trades losing 25%: -$20
lvl, why = S.decide('x', S.stats(burn), E, mode='auto', start='trial')
ok(lvl == 'paper' and 'budget' in why, f"real trades lost $20 in 30 days (over {S.LOSS_BUDGET_PCT:g}% of a $300 account): back to PAPER")
old = [T(-25.0, '2026-09-12', live=True, usd=-5.0, closed=(datetime.now(timezone.utc) - timedelta(days=40)).isoformat())
       for _ in range(4)]
ok(D(S.stats(old), 'trial') == 'trial', 'the same losses 40 days ago no longer count against the 30-day budget')
ok(D(st2, mode='paper') == 'paper' and D(st2, mode='off') == 'off' and D(empty, mode='live') == 'full',
   "set by hand: 'paper' / 'off' hold, 'live' = full size with no automatic demotion")
ok(S.stake_for('flush', 'trial', 1000) == 20.0 and S.stake_for('flush', 'full', 1000) == 100.0
   and S.stake_for('trend', 'full', 1000) == 80.0 and S.stake_for('dip', 'full', 1000) is None
   and S.stake_for('flush', 'full', 50) == 20.0,
   'stakes: trial $20; full = 10% (flush) / 8% (trend) of the account, never below trial; dip full = the AI decides')

# ── 3. levels() from the database, cached, and safe on errors ─────────────────
sc.SUPABASE_URL, sc.SUPABASE_KEY = 'https://db.test', 'a.b.c'
sc.STRATEGY_MODE = {'dip': 'auto', 'flush': 'auto', 'trend': 'auto'}
sc.STRATEGY_START = {'dip': 'paper', 'flush': 'trial', 'trend': 'paper'}
reads = []


def get(path):
    reads.append(path)
    if path.startswith('option3_trades'):
        assert 'closed_at=gte.' + S.EVIDENCE_SINCE in path, 'the record starts at EVIDENCE_SINCE'
        return [{'strategy': 'flush', 'amount_usdt': 20, 'net_pnl_usdt': 1.0, 'created_at': '2026-11-12T03:00:00+00:00',
                 'closed_at': '2026-11-14T03:00:00+00:00'}]
    return [{'strategy': 'dip', 'net_pct': -1.2, 'net_pnl_usdt': -1.2, 'opened_at': '2026-11-12T03:00:00+00:00',
             'closed_at': '2026-11-12T09:00:00+00:00'}]


S._get = get
S._CACHE.update(at=0, levels={})
L = S.levels(300.0)
ok(L['flush']['level'] == 'trial' and L['flush']['stake'] == 20.0 and L['flush']['stats']['n_live'] == 1
   and abs(L['flush']['stats']['mean'] - 5.0) < 1e-9, 'flush: trial at $20; its one real trade (+$1 on $20) counted as +5%')
ok(L['dip']['level'] == 'paper' and L['dip']['stats']['n_paper'] == 1 and L['trend']['level'] == 'paper',
   'dip and trend on paper; dip\'s paper trade is in its record')
S.levels(); S.levels()
ok(len(reads) == 2, 'two database reads, then cached (not two per call)')
S._get = lambda p: (_ for _ in ()).throw(RuntimeError('db down'))
S._CACHE['at'] = 0
with contextlib.redirect_stdout(io.StringIO()):
    L2 = S.levels()
ok(L2 is L or L2 == L, 'database down: the last known levels stand')
S._CACHE.update(at=0, levels={})
with contextlib.redirect_stdout(io.StringIO()):
    L3 = S.levels()
ok(all(v['level'] == 'paper' for v in L3.values()), 'database down with nothing known: everything paper — never real money on missing evidence')
S._get = get

# ── 4. Telegram when a strategy moves ─────────────────────────────────────────
S._CACHE.update(at=0, levels={}); TG.clear()
cache = {}
S.announce(cache, 300.0)
ok(len(TG) == 1 and 'Strategy line-up' in TG[0] and 'Crash rebound: trades REAL money, $20 a trade (trial)' in TG[0]
   and 'Dip buy: paper only' in TG[0], 'first run after the update: one message with the whole line-up')
S._CACHE['at'] = 0; TG.clear()
S.announce(cache, 300.0)
ok(not TG, 'nothing changed: no message')
cache['_levels']['dip'] = 'trial'                     # pretend dip was on trial and falls back
S._CACHE['at'] = 0
S.announce(cache, 300.0)
ok(len(TG) == 1 and 'Dip buy: trial → paper' in TG[0] and '📉' in TG[0], 'a demotion is announced with its reason')

# ── 5. routing a flush signal: real order on trial, paper otherwise ───────────
placed, papered = [], []


def fake_place(name, symbol, stake, plan, ctx, lvl, why):
    placed.append((name, symbol, stake, lvl, plan))
    return {'symbol': symbol, 'strategy': name, 'amount_usdt': stake, 'phase': 1}


sc.place_simple_trade = fake_place
paper._on = lambda: True
paper.open_trades = lambda strategy=None: [t for t in papered if strategy in (None, t['strategy'])]
paper.open_trade = lambda strategy, symbol, price, amount, kind, params, ctx=None, plan='', notify=True: \
    papered.append({'strategy': strategy, 'symbol': symbol, 'params': params}) or papered[-1]
S.oi_change_24h = lambda coin, chg: -22.0
sc.live_paused = lambda equity: None
sc.OKX_API_KEY = sc.OKX_SECRET_KEY = sc.OKX_PASSPHRASE = 'x'
sc.SYMBOLS = [f'C{i}-USDT' for i in range(7)]
tick = {f'C{i}-USDT': {'last': 90 - i, 'open24h': 100, 'vol': 5e6} for i in range(7)}


def flush(live_rows):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        S.flush_scan(tick, {}, 300.0, live_rows)
    return out.getvalue()


S._CACHE.update(at=0, levels={})
live_rows = []
log = flush(live_rows)
ok(len(placed) == S.LIVE_MAX_OPEN['flush'] and all(p[2] == 20.0 and p[3] == 'trial' for p in placed)
   and placed[0][4] == {'kind': 'hold', 'hold_hours': S.FLUSH_HOLD_HOURS},
   f"7 flushes at once, flush on trial: {len(placed)} REAL $20 trades (the cap), hardest-hit coins first")
ok(placed[0][1] == 'C6-USDT', 'the hardest-hit coin goes first')
ok(len(papered) == 3 and all(p['params'] == {'hold_hours': 48, 'stop_pct': sc.DISASTER_STOP_PCT} for p in papered),
   'the 3 the real cap turned away become paper trades (same exit, same stop): no signal is wasted as evidence')

placed.clear(); papered.clear()
sc.live_paused = lambda equity: 'live trades lost $20 in the last 30 days'
S._CACHE.update(at=0, levels={})
log = flush([])
ok(not placed and len(papered) == S.FLUSH_PAPER_MAX and 'paper instead' in log,
   'monthly loss limit hit: no real trades, the signals are paper-traded')
sc.live_paused = lambda equity: None

placed.clear(); papered.clear()
sc.STRATEGY_MODE['flush'] = 'paper'
S._CACHE.update(at=0, levels={})
flush([])
ok(not placed and len(papered) == S.FLUSH_PAPER_MAX, 'flush set to paper by hand: paper only')
sc.STRATEGY_MODE['flush'] = 'auto'

placed.clear(); papered.clear()
def refuse(*a, **k): raise sc.Option3Preflight('only $8.00 USDT free')
sc.place_simple_trade = refuse
S._CACHE.update(at=0, levels={})
flush([])
ok(not placed and len(papered) == S.FLUSH_PAPER_MAX, 'not enough USDT for a real trade: paper instead')
def blow(*a, **k): raise Exception('protective stop failed — position sold back')
sc.place_simple_trade = blow
papered.clear()
flush([])
ok(not papered, 'a real trade that failed after buying (and was sold back) gets no paper twin')
sc.place_simple_trade = fake_place

placed.clear(); papered.clear()
flush([{'symbol': 'C6-USDT', 'strategy': 'dip', 'amount_usdt': 50, 'phase': 1}])
ok('C6-USDT' not in [p[1] for p in placed] and 'C6-USDT' in [p['symbol'] for p in papered],
   'a coin already held by a real dip trade is not bought for real again (paper instead)')

placed.clear(); papered.clear()
sc.OKX_API_KEY = ''
S._CACHE.update(at=0, levels={})
flush([])
ok(not placed, 'no OKX key on the machine: never a real order')
sc.OKX_API_KEY = 'x'

# ── 6. trend: real on its live rungs, exits through close_simple_trade ────────
class _Noon(S.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 20, 12, 0, tzinfo=tz)


S.datetime = _Noon
S.TREND_COINS = ['BTC', 'ETH']
series = {'BTC-USDT': [100.0] * 200 + [110.0], 'ETH-USDT': [100.0] * 200 + [100.5]}
sc.fetch_candles = lambda sym, bar='1H', limit=100: {'closes': series[sym]}
sc.fetch_ticker = lambda sym: {'price': series[sym][-1], 'change_pct': 0.0}
sc.STRATEGY_MODE['trend'] = 'live'                    # by hand, to exercise the real path
S._CACHE.update(at=0, levels={})
placed.clear(); papered.clear(); TG.clear()
rows = []
with contextlib.redirect_stdout(io.StringIO()):
    S.trend_scan({}, 1000.0, rows)
ok(placed and placed[0][1] == 'BTC-USDT' and placed[0][2] == 80.0 and placed[0][4]['kind'] == 'rule'
   and 'BTC (REAL)' in TG[-1] and 'real money' in TG[-1],
   'trend on a live rung: a REAL buy at 8% of the account, reported as (REAL)')
closed = []
sc.close_simple_trade = lambda t, reason, cache=None: closed.append((t['symbol'], reason)) or True
series['BTC-USDT'] = [100.0] * 200 + [110.0] * 60 + [95.0]
with contextlib.redirect_stdout(io.StringIO()):
    S.trend_scan({}, 1000.0, [{'symbol': 'BTC-USDT', 'strategy': 'trend', 'amount_usdt': 80, 'phase': 1}])
ok(closed == [('BTC-USDT', 'rule')], 'the trend ends: the real position is sold through close_simple_trade')

# ── 7. the status row and the daily report ────────────────────────────────────
sc.STRATEGY_MODE = {'dip': 'auto', 'flush': 'auto', 'trend': 'auto'}
S._CACHE.update(at=0, levels={})
st = S.status()
ok(st['strategies']['flush']['level'] == 'trial' and st['bar']['min_trades'] == 30 and st['trial_stake'] == 20.0,
   'status row carries each rung, stake and record, and the bar')
lines = S.digest_lines()
ok(len(lines) == 3 and any('Crash rebound: trades REAL money, $20 a trade (trial)' in x for x in lines),
   'daily report: one line per strategy')

print(f'ALL PASSED ({N} checks)')
