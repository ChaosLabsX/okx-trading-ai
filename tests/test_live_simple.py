"""
Real-money positions of the simple strategies (flush, trend) against a simulated OKX that
keeps exact balances: a market buy, a protective stop for the whole position, an exit the
bot makes itself - and every way that can go wrong. No network, no keys, places nothing.

    python tests/test_live_simple.py

Run after any change to place_simple_trade / close_simple_trade / monitor_simple_trades.
The simulated OKX refuses what the real one refuses: selling coins frozen behind a stop,
a stop larger than the free balance, cancelling an order that is no longer live.
"""
import contextlib, io, os, sys, time
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import signal_checker as sc

N = 0


def ok(cond, what):
    global N
    assert cond, 'FAILED: ' + what
    N += 1
    print('ok   ' + what)


FEE = sc.OKX_FEE_RATE


class OKX:
    def __init__(s, usdt=500.0):
        s.price = {'SOL-USDT': 100.0, 'BTC-USDT': 75000.0}
        s.spec = {'SOL-USDT': ('0.01', '0.0001', '0.001'), 'BTC-USDT': ('0.1', '0.00000001', '0.001')}
        s.bal = {'USDT': usdt}
        s.algos, s.orders, s.calls, s.n = {}, {}, [], 0
        s.fail = set()

    def coin(s, inst):
        return inst.split('-')[0]

    def frozen(s, c):
        return sum(float(a['body']['sz']) for a in s.algos.values()
                   if a['state'] == 'live' and s.coin(a['body']['instId']) == c)

    def free(s, c):
        return s.bal.get(c, 0.0) - s.frozen(c)

    def post(s, path, body):
        s.calls.append((path.rsplit('/', 1)[-1], body))
        s.n += 1
        if path.endswith('/trade/order'):
            inst, c, px = body['instId'], s.coin(body['instId']), s.price[body['instId']]
            if body['side'] == 'buy':
                if 'buy' in s.fail:
                    raise Exception('OKX 51008: buy rejected')
                assert body.get('tgtCcy') == 'quote_ccy'
                usd = float(body['sz'])
                assert usd <= s.bal['USDT'] + 1e-9, 'buying with money the account does not have'
                sz = usd / px
                fee = -sz * FEE
                s.bal['USDT'] -= usd
                s.bal[c] = s.bal.get(c, 0.0) + sz + fee
                oid = f'B{s.n}'
                s.orders[oid] = {'accFillSz': str(sz), 'avgPx': str(px), 'fee': str(fee), 'feeCcy': c, 'state': 'filled'}
                return {'code': '0', 'data': [{'ordId': oid}]}
            if 'sell' in s.fail:
                raise Exception('OKX 51008: sell rejected')
            sz = float(body['sz'])
            assert sz <= s.free(c) + 1e-9, f'selling {sz} {c} with only {s.free(c)} free (rest frozen behind a stop)'
            fee = -sz * px * FEE
            s.bal[c] -= sz
            s.bal['USDT'] += sz * px + fee
            oid = f'S{s.n}'
            s.orders[oid] = {'accFillSz': str(sz), 'avgPx': str(px), 'fee': str(fee), 'feeCcy': 'USDT', 'state': 'filled'}
            return {'code': '0', 'data': [{'ordId': oid}]}
        if path.endswith('order-algo'):
            if 'algo' in s.fail:
                raise Exception('OKX 51000: algo rejected')
            c = s.coin(body['instId'])
            assert body['ordType'] == 'conditional' and body['side'] == 'sell' and body['slOrdPx'] == '-1'
            assert float(body['sz']) <= s.free(c) + 1e-9, 'stop larger than the free balance'
            aid = f'A{s.n}'
            s.algos[aid] = {'body': body, 'state': 'live'}
            return {'code': '0', 'data': [{'algoId': aid, 'sCode': '0'}]}
        if path.endswith('cancel-algos'):
            aid = body[0]['algoId']
            if 'cancel' in s.fail or s.algos.get(aid, {}).get('state') != 'live':
                raise Exception('OKX 51000: cancel failed')
            s.algos[aid]['state'] = 'canceled'
            if 'cancel_lost' in s.fail:
                raise Exception('Read timed out')        # went through, reply lost
            return {'code': '0', 'data': [{'algoId': aid, 'sCode': '0'}]}
        raise AssertionError(path)

    def get(s, path):
        if '/account/balance' in path:
            c = path.split('ccy=')[1]
            avail = s.bal.get('USDT', 0.0) if c == 'USDT' else s.free(c)
            return {'code': '0', 'data': [{'details': [{'ccy': c, 'availBal': str(avail)}]}]}
        if '/trade/order?' in path:
            if 'detail' in s.fail:
                raise Exception('Read timed out')
            return {'code': '0', 'data': [s.orders[path.split('ordId=')[1]]]}
        if 'orders-algo-history' in path:
            aid = path.split('algoId=')[1]
            a = s.algos.get(aid)
            if not a or a['state'] == 'live':
                return {'code': '0', 'data': []}
            row = {'algoId': aid, 'state': a['state']}
            if a.get('fill'):
                row['avgPx'] = str(a['fill'])
            return {'code': '0', 'data': [row]}
        raise AssertionError(path)

    def fire(s, aid, px):
        """The stop triggers on OKX: market-sells its coins."""
        a = s.algos[aid]
        c, sz = s.coin(a['body']['instId']), float(a['body']['sz'])
        a['state'], a['fill'] = 'effective', px
        s.bal[c] -= sz
        s.bal['USDT'] += sz * px * (1 - FEE)

    def live_stops(s):
        return [k for k, a in s.algos.items() if a['state'] == 'live']


# ── in-memory Supabase + Telegram ─────────────────────────────────────────────
DB, TG = {}, []
save_fail = [False]


class R:
    def __init__(s, code): s.status_code, s.text = code, ''


def post(url, headers=None, json=None, timeout=None, **k):
    if save_fail[0]:
        return R(500)
    assert url.endswith('/rest/v1/option3_trades')
    DB[json['id']] = dict(json, created_at=datetime.now(timezone.utc).isoformat())
    return R(201)


def patch(url, headers=None, json=None, timeout=None, **k):
    rid = url.split('id=eq.')[1]
    DB[rid].update(json)
    return R(204)


sc.requests.post, sc.requests.patch = post, patch
sc.send_telegram = lambda m: TG.append(m)
sc.time.sleep = lambda s: None
sc.OKX_API_KEY = sc.OKX_SECRET_KEY = sc.OKX_PASSPHRASE = 'x'
sc.SUPABASE_URL, sc.SUPABASE_KEY = 'https://sb.test', 'a.b.c'


def world(usdt=500.0):
    global X
    X = OKX(usdt)
    sc._okx_post, sc._okx_get = X.post, X.get
    sc._fetch_instrument_spec = lambda sym: X.spec[sym]
    sc.fetch_ticker = lambda sym: {'price': X.price[sym], 'change_pct': -9.0}
    DB.clear(); TG.clear(); save_fail[0] = False
    return X


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def place(**k):
    return quiet(sc.place_simple_trade, 'flush', 'SOL-USDT', k.get('stake', 20.0),
                 {'kind': 'hold', 'hold_hours': 48}, {'chg_24h': -9.1}, 'trial', 'test')


def age(row_id, hours):
    DB[row_id]['created_at'] = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def rows():
    return [dict(r) for r in DB.values() if r.get('phase', 1) < 3]


# ── 1. opening ────────────────────────────────────────────────────────────────
X = world()
row = place()
coins = 20.0 / 100.0 * (1 - FEE)                                   # 0.1998 SOL after the fee in coins
buy = [b for p, b in X.calls if p == 'order' and b['side'] == 'buy']
ok(len(buy) == 1 and buy[0]['sz'] == '20.00' and buy[0]['tgtCcy'] == 'quote_ccy',
   'one market buy for $20, sized in USDT')
stop = X.algos[row['sl_id']]['body']
ok(stop['sz'] == '0.1998' and stop['slTriggerPx'] == '70.00' and X.algos[row['sl_id']]['state'] == 'live',
   f"protective stop on every coin received ({stop['sz']} SOL, the fee taken in coins) at −30% ({stop['slTriggerPx']})")
r = DB[row['id']]
ok(r['strategy'] == 'flush' and r['phase'] == 1 and r['sz_half'] == '0.1998' and abs(r['amount_usdt'] - 20.0) < 1e-9
   and r['entry_price'] == 100.0 and r['entry_context']['exit'] == {'kind': 'hold', 'hold_hours': 48}
   and r['partial_tp_id'] == '' and r['sl2_id'] == '',
   'saved in option3_trades as strategy=flush, with its exit plan and no Option 3 order ids')
ok(len(TG) == 1 and 'REAL trade placed — SOL' in TG[0] and 'trial' in TG[0] and '−30%' in TG[0],
   'Telegram: REAL trade placed, trial size, protective stop')

# preflight: nothing is bought
X = world(usdt=15.0)
try:
    place(); raise AssertionError('should not trade')
except sc.Option3Preflight as e:
    ok('only $15.00 USDT free' in str(e) and not X.calls, 'not enough USDT: refused before any order')
X = world()
try:
    quiet(sc.place_simple_trade, 'flush', 'BTC-USDT', 20.0, {'kind': 'hold'}, {}, 'trial'); raise AssertionError
except sc.Option3Preflight as e:
    ok("minimum" in str(e) and not X.calls, '$20 under the coin\'s minimum order: refused before any order')

# the stop is rejected: the coins go straight back
X = world(); X.fail.add('algo')
try:
    place(); raise AssertionError
except sc.Option3Preflight:
    raise
except Exception as e:
    sells = [b for p, b in X.calls if p == 'order' and b['side'] == 'sell']
    ok('protective stop failed' in str(e) and len(sells) == 1 and X.bal['SOL'] < 0.0005 and not DB
       and 'Trade undone' in TG[-1], 'stop rejected: coins sold straight back, nothing saved, owner told')

# the database refuses the row: undone too (nobody would ever sell it otherwise)
X = world(); save_fail[0] = True
try:
    place(); raise AssertionError
except Exception as e:
    ok('not saved' in str(e) and not X.live_stops() and X.bal['SOL'] < 0.0005 and 'Trade undone' in TG[-1],
       'save failed: stop cancelled, coins sold back, owner told')

# the fill record never comes back: protect only what the account really holds
X = world(); X.fail.add('detail')
row = place()
ok(float(X.algos[row['sl_id']]['body']['sz']) <= X.bal['SOL'] + 1e-12 and DB[row['id']]['amount_usdt'] == 20.0,
   'no fill record: estimated, and never more coins protected than the account holds')

# ── 2. the timed exit ─────────────────────────────────────────────────────────
X = world()
row = place()
start_usdt = X.bal['USDT'] + 20.0                                   # before the buy
cache = {}
quiet(sc.monitor_simple_trades, rows(), cache)
ok(row['id'] in DB and DB[row['id']]['phase'] == 1 and not [c for c in X.calls if c[0] in ('cancel-algos',)],
   'before 48h: nothing touched')
age(row['id'], 48.1); X.price['SOL-USDT'] = 104.0; TG.clear()
quiet(sc.monitor_simple_trades, rows(), cache)
r = DB[row['id']]
net_true = X.bal['USDT'] - start_usdt
ok(r['phase'] == 3 and r['exit_reason'] == 'time' and abs(r['exit_price'] - 104.0) < 1e-9
   and abs(r['net_pnl_usdt'] - round(net_true, 4)) < 1e-4,
   f"after 48h: stop cancelled, coins sold, recorded P&L {r['net_pnl_usdt']:+.4f} = the account's real change {net_true:+.4f}")
ok(not X.live_stops() and X.bal['SOL'] < 1e-9 and 'REAL trade closed — SOL' in TG[-1] and 'planned hold time' in TG[-1],
   'no orders left behind, no coins left, Telegram with the result')

# the owner's own coins are never sold
X = world(); X.bal['SOL'] = 5.0
row = place(); age(row['id'], 49)
quiet(sc.monitor_simple_trades, rows(), {})
ok(abs(X.bal['SOL'] - 5.0) < 1e-9 and DB[row['id']]['phase'] == 3, 'the owner\'s own 5 SOL are untouched by the exit')

# ── 3. the stop fires on OKX ──────────────────────────────────────────────────
X = world()
row = place(); start_usdt = X.bal['USDT'] + 20.0
X.fire(row['sl_id'], 69.9); TG.clear()
quiet(sc.monitor_simple_trades, rows(), {})
r = DB[row['id']]
ok(r['exit_reason'] == 'stop' and abs(r['net_pnl_usdt'] - round(X.bal['USDT'] - start_usdt, 4)) < 1e-4
   and not [b for p, b in X.calls if p == 'order' and b['side'] == 'sell'],
   f"stop fired at 69.9: recorded {r['net_pnl_usdt']:+.2f} (exact), no extra sale")
ok('protective stop' in TG[-1] and '🔴' in TG[-1], 'Telegram says the protective stop closed it')

# ── 4. the stop was cancelled by hand on OKX ──────────────────────────────────
X = world()
row = place(); X.algos[row['sl_id']]['state'] = 'canceled'; TG.clear()
quiet(sc.monitor_simple_trades, rows(), {})
ok(DB[row['id']]['exit_reason'] == 'cancelled' and not [b for p, b in X.calls if p == 'order' and b['side'] == 'sell']
   and 'yours to manage' in TG[-1], 'stop cancelled on OKX by the owner: the bot stops managing it, sells nothing')

# ── 5. an exit interrupted between the cancel and the sale ────────────────────
X = world()
row = place(); age(row['id'], 50)
DB[row['id']]['trailing_id'] = 'exiting:time'
X.algos[row['sl_id']]['state'] = 'canceled'                       # the cancel went through, then the run died
quiet(sc.monitor_simple_trades, rows(), {})
ok(DB[row['id']]['exit_reason'] == 'time' and X.bal['SOL'] < 1e-9,
   "interrupted exit (marker 'exiting', stop already cancelled): finished, NOT mistaken for the owner's cancel")

# the cancel's reply is lost but it went through
X = world()
row = place(); age(row['id'], 50); X.fail.add('cancel_lost')
quiet(sc.monitor_simple_trades, rows(), {})
ok(DB[row['id']]['exit_reason'] == 'time', 'cancel timed out but went through: OKX is asked, the exit completes')

# the stop fires in the instant of the exit
X = world()
row = place(); age(row['id'], 50)
X.fire(row['sl_id'], 70.0)
r0 = dict(DB[row['id']])
quiet(sc.close_simple_trade, r0, 'time')
ok(DB[row['id']]['exit_reason'] == 'stop' and not [b for p, b in X.calls if p == 'order' and b['side'] == 'sell'],
   'stop fired just before the exit: recorded as a stop, nothing sold twice')

# OKX refuses the cancel: nothing sold, retried later
X = world()
row = place(); age(row['id'], 50); X.fail.add('cancel')
res = quiet(sc.close_simple_trade, dict(DB[row['id']]), 'time')
ok(res is False and DB[row['id']]['phase'] == 1 and X.algos[row['sl_id']]['state'] == 'live'
   and not [b for p, b in X.calls if p == 'order' and b['side'] == 'sell'],
   'cancel refused while the stop is live: nothing sold, the stop still protects, retried next pass')

# ── 6. the sale is rejected ───────────────────────────────────────────────────
X = world()
row = place(); age(row['id'], 50); X.fail.add('sell'); TG.clear()
cache = {}
quiet(sc.monitor_simple_trades, rows(), cache)
r = DB[row['id']]
ok(r['phase'] == 1 and r['sl_id'] and X.algos[r['sl_id']]['state'] == 'live' and r['trailing_id'] == ''
   and 'Exit delayed' in TG[-1], 'sale rejected: the stop is put straight back, the owner told once')
calls = len(X.calls)
quiet(sc.monitor_simple_trades, rows(), cache)
ok(len(X.calls) == calls + 0 or all(p != 'cancel-algos' for p, _ in X.calls[calls:]),
   f'and it waits {sc.SIMPLE_RETRY_SECS // 60} minutes before trying again (no cancel/restore churn every minute)')
cache['_simple_retry'][row['id']] -= sc.SIMPLE_RETRY_SECS + 1; X.fail.discard('sell')
quiet(sc.monitor_simple_trades, rows(), cache)
ok(DB[row['id']]['exit_reason'] == 'time', 'after the wait the sale goes through')

# sale AND restore rejected: URGENT, then protection comes back first
X = world()
row = place(); age(row['id'], 1); X.fail.update({'sell'})
r0 = dict(DB[row['id']])
X.fail.add('algo')
quiet(sc.close_simple_trade, r0, 'rule', {})
ok(DB[row['id']]['sl_id'] == '' and 'URGENT' in TG[-1], 'sale and the stop both rejected: URGENT message, the row knows it is unprotected')
X.fail.clear(); TG.clear()
quiet(sc.monitor_simple_trades, rows(), {})
r = DB[row['id']]
ok(r['phase'] == 1 and r['sl_id'] and X.algos[r['sl_id']]['state'] == 'live' and 'protected again' in TG[-1],
   'next pass: the stop is placed again before anything else')

# ── 7. the dip strategy's machinery never touches these rows ──────────────────
X = world()
row = place()
before = len(X.calls)
quiet(sc.monitor_option3_trades, rows())
ok(len(X.calls) == before, 'monitor_option3_trades ignores flush rows (no OCO lookups, no orders)')
ok(sc._is_option3({'strategy': 'dip'}) and sc._is_option3({}) and not sc._is_option3({'strategy': 'flush'}),
   'rows without a strategy (manual / pre-2026-11-10) belong to the dip strategy')

print(f'ALL PASSED ({N} checks)')
