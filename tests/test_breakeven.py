"""
Break-even stop (_maybe_move_to_breakeven and the exits after it) against a
simulated OKX + Supabase. No network, no keys, places nothing.

    python tests/test_breakeven.py

Run after any change to order handling in signal_checker.py: this is real-money
code that cannot be exercised against OKX from the development machine.
"""
import io, contextlib, os, sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # repo root
import signal_checker as sc

sc.time.sleep = lambda s: None
sc.OKX_API_KEY = sc.OKX_SECRET_KEY = sc.OKX_PASSPHRASE = 'x'
sc.SUPABASE_URL, sc.SUPABASE_KEY = 'https://sb.test', 'k'


class FakeOKX:
    def __init__(s):
        s.algos, s.n, s.plan, s.calls = {}, 0, [], []
        s.fail_cancel, s.cancel_timeout_ok, s.fail_market = set(), set(), False

    def add(s, aid, body, state='live', fill=None):
        s.algos[aid] = {'body': body, 'state': state, 'fill': fill}

    def post(s, path, body):
        s.calls.append((path.rsplit('/', 1)[-1], body))
        if path.endswith('cancel-algos'):
            aid = body[0]['algoId']
            if aid in s.fail_cancel or s.algos.get(aid, {}).get('state') != 'live':
                raise Exception('OKX 51000: cancel failed')
            s.algos[aid]['state'] = 'canceled'
            if aid in s.cancel_timeout_ok:
                raise Exception('Read timed out')          # went through, reply lost
            return {'code': '0', 'data': [{'algoId': aid, 'sCode': '0'}]}
        if path.endswith('order-algo'):
            if s.plan and s.plan.pop(0) == 'fail':
                raise Exception('OKX 51008: order rejected')
            s.n += 1; aid = f'NEW{s.n}'
            s.add(aid, body)
            return {'code': '0', 'data': [{'algoId': aid, 'sCode': '0'}]}
        if path.endswith('/trade/order'):
            if s.fail_market:
                raise Exception('market sell rejected')
            return {'code': '0', 'data': [{'ordId': 'MKT1'}]}
        raise AssertionError(path)

    def get(s, path):
        if 'orders-algo-history' in path:
            aid = path.split('algoId=')[1]
            a = s.algos.get(aid)
            if not a or a['state'] == 'live':
                return {'code': '0', 'data': []}
            row = {'algoId': aid, 'state': a['state']}
            if a['fill']:
                row['avgPx'] = str(a['fill'])
            return {'code': '0', 'data': [row]}
        if '/trade/order?' in path:
            return {'code': '0', 'data': [{'avgPx': '0'}]}
        raise AssertionError(path)

    def live(s):
        return {k: v['body'] for k, v in s.algos.items() if v['state'] == 'live'}


patches, tg = [], []
patch_fail = [0]


class Resp:
    def __init__(s, code): s.status_code = code


def fake_patch(url, headers=None, json=None, timeout=None):
    if patch_fail[0] > 0:
        patch_fail[0] -= 1
        return Resp(500)
    patches.append(json)
    return Resp(204)


sc.requests.patch = fake_patch
sc.send_telegram = lambda m: tg.append(m)
PRICE = {'px': 100.0, 'high': 100.0}
sc.fetch_ticker = lambda s: {'price': PRICE['px'], 'change_pct': 0}
sc.fetch_candles = lambda s, bar='1H', limit=100: {'highs': [PRICE['high']] * limit, 'lows': [], 'closes': [],
                                                    'opens': [], 'volumes': []}


def setup(sl_pct=3.0, sl2=True):
    global ok
    ok = FakeOKX()
    sc._okx_post, sc._okx_get = ok.post, ok.get
    patches.clear(); tg.clear(); patch_fail[0] = 0
    ok.add('OCO1', {'ordType': 'oco', 'tpTriggerPx': '104.00000000', 'slTriggerPx': '97.00000000'})
    if sl2:
        ok.add('SL21', {'ordType': 'conditional', 'slTriggerPx': '97.00000000'})
    return {'id': 'OCO1', 'symbol': 'SOL-USDT', 'entry_price': 100.0, 'partial_tp_id': 'OCO1', 'sl_id': 'OCO1',
            'sl2_id': 'SL21' if sl2 else '', 'sz_half': '0.5', 'partial_tp_pct': 4.0, 'sl_pct': sl_pct,
            'trailing_pct': 1.5, 'phase': 1, 'trailing_id': ''}


def run(trade):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        r = sc._maybe_move_to_breakeven(trade)
    return r, out.getvalue()


def check(cond, msg):
    if not cond:
        print('FAIL', msg); sys.exit(1)
    print('ok  ', msg)


assert sc.BREAKEVEN_TRIGGER_FRAC == 0.7
BE = sc._be_price(100.0)                      # ~100.3: entry + both fees + the slippage margin
BE_S = f'{BE:.8f}'
exact = 100.0 * (1 + sc.OKX_FEE_RATE) / (1 - sc.OKX_FEE_RATE)
check(abs(2 * sc._exit_pnl(100.0, exact, 0.5)[0]) < 1e-9 and 100.25 < BE < 100.35,
      f'break-even price: selling both halves at entry+fees nets exactly $0; stop sits at {BE:.4f}')
# trigger = 100 * (1 + 0.7 * 4%) = 102.8
t = setup(); PRICE.update(px=102.0, high=102.7)
r, _ = run(t)
check(r is False and not patches and not tg and all(c[0] != 'order-algo' for c in ok.calls),
      'below the 70% line (102.7 < 102.8): nothing touched')

t = setup(); PRICE.update(px=101.0, high=102.9)
r, out = run(t)
live = ok.live()
check(r is True and set(live) == {'NEW1', 'NEW2'}, 'touch via 1m high counts (ticker 101, high 102.9): both orders replaced')
check(live['NEW1'] == {'instId': 'SOL-USDT', 'tdMode': 'cash', 'side': 'sell', 'sz': '0.50000000', 'ordType': 'conditional',
                       'slTriggerPx': BE_S, 'slOrdPx': '-1', 'slTriggerPxType': 'last'},
      '2nd half: stop at the break-even price, not at entry')
check(live['NEW2']['ordType'] == 'oco' and live['NEW2']['tpTriggerPx'] == '104.00000000'
      and live['NEW2']['slTriggerPx'] == BE_S and live['NEW2']['sz'] == '0.50000000',
      '1st half: OCO keeps TP 104, stop moved to the break-even price')
order = [c[0] for c in ok.calls]
check(order == ['cancel-algos', 'order-algo', 'cancel-algos', 'order-algo'], 'second half swapped first, then the OCO')
check(patches == [{'sl2_id': 'NEW1', 'partial_tp_id': 'NEW2', 'sl_id': 'NEW2', 'sl_pct': 0}], 'row updated: new ids, sl_pct 0')
check(len(tg) == 1 and 'Break-Even' in tg[0] and '−3.0%' in tg[0] and 'about $0' in tg[0] and 'plus fees' in tg[0],
      'one Telegram: stop moved to break-even, "about $0" instead of the old "about −0.2% in fees"')

sc.BREAKEVEN_TRIGGER_FRAC = 0.05             # misconfigured: 70% line would sit below the break-even price
t = setup(); PRICE.update(px=103, high=103)
r, out = run(t)
check(r is False and not any(c[0] == 'order-algo' for c in ok.calls), 'trigger below the break-even price: refuses to move')
sc.BREAKEVEN_TRIGGER_FRAC = 0.7
t = setup(); PRICE.update(px=101.0, high=102.9); run(t)

t['sl_pct'] = 0; t['partial_tp_id'] = t['sl_id'] = 'NEW2'; t['sl2_id'] = 'NEW1'
calls_before = len(ok.calls)
r, _ = run(t)
check(r is False and len(ok.calls) == calls_before, 'already at break-even: never moved twice')

t = setup(sl2=False); PRICE.update(px=103.5, high=103.5)
r, _ = run(t)
check(r is False and not ok.calls, 'legacy trade without a 2nd-half stop: left alone')

t = setup(); PRICE.update(px=103, high=103); ok.fail_cancel.add('SL21')
r, _ = run(t)
check(r is False and set(ok.live()) == {'OCO1', 'SL21'} and not patches, 'cancel refused: nothing changed, retried next pass')

t = setup(); PRICE.update(px=103, high=103); ok.cancel_timeout_ok.add('SL21')
r, _ = run(t)
check(r is True and set(ok.live()) == {'NEW1', 'NEW2'} and patches[-1]['sl_pct'] == 0,
      'cancel reply lost but OKX shows it cancelled: carries on (never assumes a dead stop protects)')

t = setup(); PRICE.update(px=103, high=103); ok.plan = ['fail']
r, _ = run(t)
live = ok.live()
check(r is True and set(live) == {'OCO1', 'NEW1'} and live['NEW1']['slTriggerPx'] == '97.00000000',
      '2nd-half stop rejected: original 97 stop restored, OCO untouched')
check(patches == [{'sl2_id': 'NEW1'}], 'restored stop id recorded, still not at break-even (retries next pass)')

t = setup(); PRICE.update(px=103, high=103); ok.plan = ['fail', 'fail']
r, _ = run(t)
check(r is True and not ok.live() and ('order', ) and any(c[0] == 'order' and c[1]['sz'] == '1.00000000' for c in ok.calls),
      '2nd-half stop AND restore rejected: OCO cancelled, whole position (2 x 0.5) market-sold')
check(patches and patches[-1].get('exit_reason') == 'error' and 'Closed Early' in tg[-1], 'trade closed as error, Telegram explains')

t = setup(); PRICE.update(px=103, high=103); ok.plan = ['fail', 'fail']; ok.fail_market = True
r, _ = run(t)
check('URGENT' in tg[-1] and 'NO stop loss' in tg[-1], 'and if the market sell also fails: URGENT manual-action Telegram')

t = setup(); PRICE.update(px=103, high=103); ok.fail_cancel.add('OCO1')
r, _ = run(t)
check(r is True and set(ok.live()) == {'OCO1', 'NEW1'} and patches == [{'sl2_id': 'NEW1'}] and not tg,
      'OCO cancel refused (e.g. TP just filled): 2nd half at entry recorded, no "moved" message, no sl_pct change')

t = setup(); PRICE.update(px=103, high=103); ok.plan = ['ok', 'fail']
r, _ = run(t)
live = ok.live()
check(set(live) == {'NEW1', 'NEW2'} and live['NEW2']['slTriggerPx'] == '97.00000000'
      and patches == [{'sl2_id': 'NEW1', 'partial_tp_id': 'NEW2', 'sl_id': 'NEW2'}],
      'OCO at entry rejected: original OCO restored and recorded, half 2 at entry, retried next pass')

t = setup(); PRICE.update(px=103, high=103); ok.plan = ['ok', 'fail', 'fail']
r, _ = run(t)
check(not ok.live() and patches[-1].get('exit_reason') == 'error', 'OCO and its restore rejected: unwind sells everything')

t = setup(); PRICE.update(px=103, high=103); patch_fail[0] = 3
r, _ = run(t)
check(set(ok.live()) == {'NEW1', 'NEW2'} and any('NOT recorded' in m for m in tg), 'database update fails 3x: URGENT Telegram with the new ids')

# ── exits after the move, through the real monitor ────────────────────────────
def monitor(trade):
    sc._fetch_option3_trades = lambda: [trade]
    with contextlib.redirect_stdout(io.StringIO()) as out:
        sc.monitor_option3_trades()
    return out.getvalue()


def be_trade():
    t = setup()
    ok.algos.clear()
    ok.add('NEW2', {'ordType': 'oco'}); ok.add('NEW1', {'ordType': 'conditional'})
    t.update({'partial_tp_id': 'NEW2', 'sl_id': 'NEW2', 'sl2_id': 'NEW1', 'sl_pct': 0})
    return t


for fill, label in ((BE - 0.05, 'just below the stop'), (BE + 0.05, 'a hair above it')):
    t = be_trade()
    ok.algos['NEW2'].update(state='effective', fill=fill); ok.algos['NEW1'].update(state='effective', fill=fill)
    monitor(t)
    closed = [p for p in patches if p.get('phase') == 3]
    check(closed and closed[-1]['exit_reason'] == 'be_stop' and 'Break-Even Stop' in tg[-1],
          f'break-even stop fills {label} ({fill:.2f}): recorded be_stop, not mistaken for the take-profit')

for fill, want, label in ((exact, '$0.00 USDT after fees', 'exactly at entry+fees'),
                          (BE, '+$0.10 USDT after fees', 'at the stop (margin kept)'),
                          (BE * (1 - 0.00092), '+$0.01 USDT after fees', 'with 90th-percentile slippage'),
                          (100.0, '−$0.20 USDT after fees', 'at entry (the old stop, TRX case)')):
    t = be_trade()
    ok.algos['NEW2'].update(state='effective', fill=fill); ok.algos['NEW1'].update(state='effective', fill=fill)
    monitor(t)
    check(f'Result: {want}' in tg[-1], f'exit {label}: Telegram says "Result: {want}"')

t = be_trade(); ok.algos['NEW2'].update(state='effective'); ok.algos['NEW1'].update(state='effective')
monitor(t)
closed = [p for p in patches if p.get('phase') == 3][-1]
check(closed['exit_reason'] == 'be_stop' and abs(closed['exit_price'] - BE) < 1e-9 and closed['net_pnl_usdt'] >= 0,
      'fill prices unknown: estimated at the break-even stop (net >= $0), not at entry or the old -3% stop')

t = be_trade(); ok.algos['NEW2'].update(state='effective', fill=104.0); ok.algos['NEW1'].update(state='effective')
monitor(t)
closed = [p for p in patches if p.get('phase') == 3][-1]
check(closed['exit_reason'] == 'break_even' and closed['net_pnl_usdt'] > 0 and 'exited at break-even' in tg[-1],
      'TP filled then 2nd half hit the break-even stop (fill unknown): break_even, net positive')

t = setup(); ok.algos['OCO1'].update(state='effective', fill=97.0); ok.algos['SL21'].update(state='effective', fill=97.0)
monitor(t)
closed = [p for p in patches if p.get('phase') == 3][-1]
check(closed['exit_reason'] == 'sl' and 'Stop Loss Hit' in tg[-1], 'a normal stop-loss is still recorded as sl')

t = setup(); ok.algos['OCO1'].update(state='effective', fill=104.0)
sc._swap_sl2_to_trailing = lambda tr: ('trailing', 'TRAIL1')
monitor(t)
check(any(p.get('phase') == 2 and p.get('trailing_id') == 'TRAIL1' for p in patches), 'a normal take-profit still goes to phase 2')

t = setup(); PRICE.update(px=103, high=103)
out = monitor(t)
check(set(ok.live()) == {'NEW1', 'NEW2'} and '[BE]' in out, 'monitor pass in phase 1 performs the move when the line is crossed')
t = setup(); PRICE.update(px=101, high=101)
out = monitor(t)
check('waiting for TP or SL' in out and set(ok.live()) == {'OCO1', 'SL21'}, 'and just waits when it is not')

g = sc._grade_exit('be_stop', 100.0, 104.0, {'highs': [107.0], 'lows': [99.0], 'closes': [106.0]})
check(g['verdict'] == 'left_money', 'journal grades be_stop as an exit (left_money), never as a stop-loss shakeout')
print('ALL PASSED')
