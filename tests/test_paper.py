"""
Paper trading does what it says: the same exits the research measured, real candles,
no orders. No network: OKX, Supabase and Telegram are replaced by an in-memory fake.

    python tests/test_paper.py

Run after touching paper.py, run_scan's strategy modes, or the journal of refused signals.
"""
import contextlib, io, os, random, sys, time
from array import array

sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'research'))
import signal_checker as sc
import paper
import replay

N = 0


def ok(cond, what):
    global N
    assert cond, 'FAILED: ' + what
    N += 1
    print('ok   ' + what)


# ── fake world ────────────────────────────────────────────────────────────────
class Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._d = status, data
        self.text = '' if data is None else 'x'
    def json(self): return self._d
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError(f'HTTP {self.status_code}')


class World:
    """Routes every HTTP call the worker makes. Anything not listed fails the test."""
    def __init__(self):
        self.calls, self.db, self.next_id = [], {'paper_trades': [], 'skipped_setups': [], 'bot_status': []}, 1
        self.candles, self.oi, self.telegram = {}, {}, []

    def request(self, method, url, headers=None, json=None, timeout=None, params=None, data=None):
        self.calls.append((method, url))
        assert '/api/v5/trade/' not in url and '/api/v5/account/' not in url, f'PRIVATE OKX CALL: {url}'
        if 'api.telegram.org' in url:
            self.telegram.append(json['text']); return Resp(200, {})
        if '/rest/v1/' in url:
            table = url.split('/rest/v1/')[1].split('?')[0]
            q = url.split('?', 1)[1] if '?' in url else ''
            rows = self.db.setdefault(table, [])
            if method == 'GET':
                out = rows
                if 'closed_at=is.null' in q: out = [r for r in out if not r.get('closed_at')]
                if 'closed_at=not.is.null' in q: out = [r for r in out if r.get('closed_at')]
                for part in q.split('&'):
                    if part.startswith('strategy=eq.'): out = [r for r in out if r.get('strategy') == part[12:]]
                    if part.startswith('exit_reason=eq.'): out = [r for r in out if r.get('exit_reason') == part[15:]]
                return Resp(200, list(out))
            if method == 'POST':
                new = json if isinstance(json, list) else [json]
                for r in new:
                    r = dict(r); r['id'] = self.next_id; self.next_id += 1
                    r.setdefault('opened_at', time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime()))
                    rows.append(r)
                return Resp(201, [rows[-1]])
            if method == 'PATCH':
                rid = int(q.split('id=eq.')[1].split('&')[0])
                for r in rows:
                    if r['id'] == rid: r.update(json)
                return Resp(204, None)
        if '/market/candles' in url:
            inst = url.split('instId=')[1].split('&')[0]
            bar = url.split('bar=')[1].split('&')[0]
            return Resp(200, {'code': '0', 'data': [[str(t), str(o), str(h), str(l), str(c), '1', '1', '1', '1']
                                                    for t, o, h, l, c in reversed(self.candles.get((inst, bar), []))]})
        if 'open-interest-volume' in url:
            coin = url.split('ccy=')[1].split('&')[0]
            return Resp(200, {'code': '0', 'data': self.oi.get(coin, [])})
        if '/market/ticker?' in url:
            return Resp(200, {'code': '0', 'data': [{'last': '100', 'open24h': '100'}]})
        raise AssertionError(f'unexpected request: {method} {url}')


W = World()
sc.requests.request = W.request
sc.requests.get = lambda url, **kw: W.request('GET', url, **kw)
sc.requests.post = lambda url, **kw: W.request('POST', url, **kw)
sc.requests.patch = lambda url, **kw: W.request('PATCH', url, **kw)
sc.SUPABASE_URL, sc.SUPABASE_KEY = 'https://db.test', 'a.b.c'
sc.TELEGRAM_BOT_TOKEN, sc.TELEGRAM_CHAT_ID = 't', '1'
paper.time.sleep = lambda s: None
quiet = lambda: contextlib.redirect_stdout(io.StringIO())


# ── 1. the paper exits ARE the exits the research measured ────────────────────
class FakeCoin:
    def __init__(self, o, h, l, c):
        self.m = {'o': array('d', o), 'h': array('d', h), 'l': array('d', l), 'c': array('d', c)}
        self.n = len(c)


random.seed(7)
old_slip, old_fee = paper.PAPER_SLIP, replay.FEE
paper.PAPER_SLIP = 0.0                          # replay.py has no slippage term; compare like with like
kinds = {}
for trial in range(400):
    px, o, h, l, c = 100.0, [], [], [], []
    vol = random.choice([0.0006, 0.0015, 0.003])
    for i in range(3000):
        op = px
        steps = [op * (1 + random.gauss(0, vol)) for _ in range(4)]
        cl = steps[-1]
        o.append(op); h.append(max(op, *steps)); l.append(min(op, *steps)); c.append(cl); px = cl
    ex = {'tp': random.choice([1.5, 2.2, 3.4, 6.0]), 'sl': random.choice([2.0, 3.1, 5.0]), 'trail': random.choice([1.0, 1.4, 2.5])}
    ex['trail'] = min(ex['trail'], ex['tp'] - 0.5)
    ref = replay.simulate(FakeCoin(o, h, l, c), 0, ex, be_frac=sc.BREAKEVEN_TRIGGER_FRAC)
    st = paper.new_state(0)
    candles = [(60_000 * (i + 1), o[i], h[i], l[i], c[i]) for i in range(3000)]
    # fed in uneven chunks, the way the worker sees them (a few minutes per scan)
    out, i = None, 0
    while out is None and i < len(candles):
        k = random.choice([1, 2, 5, 60, 400])
        out = paper.step_option3(o[0], ex, st, candles[i:i + k]); i += k
    if ref[1] == 'open':
        assert out is None, f'trial {trial}: replay still open, paper exited {out}'
        kinds['open'] = kinds.get('open', 0) + 1
        continue
    name = {'be': 'be_stop'}.get(ref[1], ref[1])
    assert out and out['reason'] == name and abs(out['frac'] - ref[2]) < 1e-12 and out['ts'] == 60_000 * (ref[0] + 1), \
        f'trial {trial}: replay {ref} vs paper {out}'
    kinds[name] = kinds.get(name, 0) + 1
paper.PAPER_SLIP = old_slip
ok(all(kinds.get(k, 0) >= 15 for k in ('sl', 'be_stop', 'tp_trail')),
   f'400 random trades: paper exits match research/replay.py to the last decimal {kinds}')

# slippage and fees are charged: a flat round trip costs both fees plus both slips
e = 100 * (1 + paper.PAPER_SLIP)
flat = paper._net(e, 100.0)
ok(abs(flat - (-(2 * sc.OKX_FEE_RATE + 2 * paper.PAPER_SLIP))) < 2e-6, f'a flat round trip nets {flat:.4%} (fees + slippage)')

# ── 2. hold-for-N-hours ──────────────────────────────────────────────────────
st = paper.new_state(0)
cs = [(60_000 * i, 100, 101, 99, 100.5) for i in range(1, 200)]
out = paper.step_hold(100.0, {'hold_hours': 2}, st, cs, opened_ms=0, bar_ms=60_000)
ok(out and out['reason'] == 'time' and out['ts'] == 60_000 * 119 and out['price'] == 100.5,
   'hold trade sells at the close of the candle that reaches the deadline')
ok(abs(st['mae'] + 1.0) < 1e-9 and abs(st['mfe'] - 1.0) < 1e-9, 'and records the worst and best point on the way')
st = paper.new_state(0)
out = paper.step_hold(100.0, {'hold_hours': 2, 'stop_pct': 5}, st,
                      [(60_000, 100, 100, 99, 99), (120_000, 96, 96, 90, 91)], opened_ms=0, bar_ms=60_000)
ok(out['reason'] == 'stop' and out['price'] == 95.0, 'optional far stop fills at the stop price')

# ── 3. monitor: real candles in, one write out, never an order ────────────────
now_ms = int(time.time() * 1000)
opened = now_ms - 30 * 60_000
W.db['paper_trades'] = [{'id': 1, 'strategy': 'dip', 'symbol': 'SOL-USDT', 'kind': 'option3',
                         'opened_at': time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime(opened / 1000)),
                         'entry_price': 100.0, 'amount_usdt': 150.0,
                         'params': {'tp': 2.0, 'sl': 3.0, 'trail': 1.0}, 'state': {}}]
W.next_id = 2
base = opened // 60_000 * 60_000
W.candles[('SOL-USDT', '1m')] = [(base + 60_000 * i, 100, 100.4, 99.6, 100) for i in range(1, 11)]
cache = {}
paper._OPEN = None
with quiet():
    paper.monitor(cache)
ok(W.db['paper_trades'][0].get('closed_at') is None and cache['_paper']['1']['last_ts'] == base + 600_000,
   'monitor steps an open paper trade and keeps its progress in the cache file')
writes = [c for c in W.calls if c[0] in ('POST', 'PATCH') and '/rest/v1/' in c[1]]
ok(not writes, 'with no change of phase it writes nothing to the database')
W.candles[('SOL-USDT', '1m')] += [(base + 60_000 * 11, 100, 100.2, 96.5, 97)]
with quiet():
    paper.monitor(cache)
row = W.db['paper_trades'][0]
ok(row['exit_reason'] == 'sl' and abs(row['exit_price'] - 97.0) < 1e-9 and row['net_pct'] < -3.0
   and abs(row['net_pnl_usdt'] - row['net_pct'] / 100 * 150) < 1e-3,
   f"stop-loss closes it: {row['net_pct']}% of $150 = {row['net_pnl_usdt']}")
ok('PAPER' in W.telegram[-1] and 'No real money' in W.telegram[-1], 'Telegram message is marked PAPER / no real money')
ok('1' not in cache['_paper'] and not paper.open_trades(), 'closed trade leaves the cache and the open list')

# a lost cache file: the trade is rebuilt from its entry time
W.db['paper_trades'].append({'id': 5, 'strategy': 'dip', 'symbol': 'SOL-USDT', 'kind': 'option3',
                             'opened_at': time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime(opened / 1000)),
                             'entry_price': 100.0, 'amount_usdt': 100.0,
                             'params': {'tp': 2.0, 'sl': 3.0, 'trail': 1.0}, 'state': {}})
W.candles[('SOL-USDT', '1m')] = [(base + 60_000 * i, 100, 102.5 if i == 4 else 100.3, 99.8, 100.2) for i in range(1, 9)]
paper._OPEN = None
with quiet():
    paper.monitor({})
ok(W.db['paper_trades'][1]['state'].get('phase') == 2, 'no cache: rebuilt from the entry, target hit recorded as phase 2')

# the database is unreachable at the moment a trade ends: the exit is kept and retried,
# not recomputed from whatever the price does afterwards
W.db['paper_trades'] = [{'id': 7, 'strategy': 'dip', 'symbol': 'SOL-USDT', 'kind': 'option3',
                         'opened_at': time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime(opened / 1000)),
                         'entry_price': 100.0, 'amount_usdt': 100.0,
                         'params': {'tp': 2.0, 'sl': 3.0, 'trail': 1.0}, 'state': {}}]
W.candles[('SOL-USDT', '1m')] = [(base + 60_000, 100, 100.2, 96.0, 96.5)]
paper._OPEN = None
cache = {}
real_request = sc.requests.request
def flaky(method, url, **kw):
    if method == 'PATCH' and 'paper_trades' in url:
        return Resp(503, {'message': 'down'})
    return W.request(method, url, **kw)
sc.requests.request = flaky
with quiet():
    paper.monitor(cache)
ok(W.db['paper_trades'][0].get('closed_at') is None and cache['_paper']['7']['pending_exit']['reason'] == 'sl',
   'database down as the stop is hit: the trade stays open and the exit is remembered')
sc.requests.request = real_request
W.candles[('SOL-USDT', '1m')] += [(base + 120_000, 97, 130, 97, 129)]          # price then rockets
with quiet():
    paper.monitor(cache)
ok(W.db['paper_trades'][0]['exit_reason'] == 'sl' and abs(W.db['paper_trades'][0]['exit_price'] - 97.0) < 1e-9
   and '7' not in cache['_paper'], 'next pass saves THAT exit (stop at 97), not a new one from later prices')

# ── 4. flush: needs BOTH the fall and the open-interest collapse ──────────────
sc.SYMBOLS = ['AAA-USDT', 'BBB-USDT', 'CCC-USDT', 'DDD-USDT']
tick = lambda last, o24: {'last': last, 'open24h': o24, 'vol': 5e6}
tickers = {'AAA-USDT': tick(90, 100), 'BBB-USDT': tick(90, 100), 'CCC-USDT': tick(97, 100),
           'DDD-USDT': {'last': 80, 'open24h': 100, 'vol': 1e5}, 'BTC-USDT': tick(95, 100)}
rows = lambda now, then: [[str(i), str(now if i == 0 else then), '0'] for i in range(30)]
W.oi = {'AAA': rows(72, 100), 'BBB': rows(88, 100), 'CCC': rows(50, 100), 'DDD': rows(50, 100)}
ok(abs(paper.oi_change_24h('AAA', -10.0) + 20.0) < 1e-9,
   'open interest -28% in dollars with price -10% = -20% in coins')
W.db['paper_trades'] = []; paper._OPEN = None; W.telegram.clear(); W.calls.clear()
cache = {}
with quiet():
    paper.flush_scan(tickers, cache)
opened_syms = [r['symbol'] for r in W.db['paper_trades']]
ok(opened_syms == ['AAA-USDT'], f'only the coin with fall >= 8% AND open interest <= -15% is bought: {opened_syms}')
r0 = W.db['paper_trades'][0]
ok(r0['kind'] == 'hold' and r0['params'] == {'hold_hours': paper.FLUSH_HOLD_HOURS} and r0['strategy'] == 'flush'
   and abs(r0['entry_price'] - 90 * (1 + paper.PAPER_SLIP)) < 1e-9, 'as a 48h hold, entered at the price plus slippage')
ok(sum('open-interest-volume' in u for _, u in W.calls) == 2,
   'open interest is looked up only for liquid coins already down 8% (2 of 4)')
W.calls.clear()
with quiet():
    paper.flush_scan(tickers, cache)
ok(len(W.db['paper_trades']) == 1 and not any('open-interest-volume' in u for _, u in W.calls),
   'same hour, same coins: no second lookup, no second trade')

# ── 5. trend: in above the band, out below it, once a day ─────────────────────
class _Noon(paper.datetime):                    # the scan waits out the first 5 minutes of the UTC day;
    @classmethod                                # pin the clock so this test passes at midnight too
    def now(cls, tz=None):
        return cls(2026, 11, 10, 12, 0, tzinfo=tz)


paper.datetime = _Noon
series = {}
sc.fetch_candles = lambda sym, bar='1H', limit=100: {'closes': series[sym]}
sc.fetch_ticker = lambda sym: {'price': series[sym][-1], 'change_pct': 0.0}
paper.TREND_COINS = ['BTC', 'ETH']
series['BTC-USDT'] = [100.0] * 200 + [110.0]          # 10% above its average
series['ETH-USDT'] = [100.0] * 200 + [100.5]          # inside the band
W.db['paper_trades'] = []; paper._OPEN = None; W.telegram.clear()
cache = {}
with quiet():
    paper.trend_scan(cache); paper.trend_scan(cache)
ok([r['symbol'] for r in W.db['paper_trades']] == ['BTC-USDT'] and cache['_trend']['day'],
   'trend buys the coin that closed 2% above its 50-day average, once per day')
ok(len(W.telegram) == 1 and 'Bought: BTC' in W.telegram[0] and 'Holding 1 of 2' in W.telegram[0]
   and 'No real money' in W.telegram[0], 'and reports the whole daily check in ONE message')
series['BTC-USDT'] = [100.0] * 200 + [110.0] * 60 + [95.0]
cache['_trend']['day'] = 'yesterday'
with quiet():
    paper.trend_scan(cache)
ok(W.db['paper_trades'][0]['exit_reason'] == 'rule' and W.db['paper_trades'][0]['net_pct'] < 0
   and 'Sold (trend ended): BTC' in W.telegram[-1], 'and sells it after a daily close 2% below')

# ── 6. run_scan: paper mode never reaches the order code ──────────────────────
import signal_checker as live                      # same module object; alias for readability
candle = {'opens': [1.0] * 100, 'highs': [1.02] * 100, 'lows': [0.98] * 100, 'closes': [1.0] * 100, 'volumes': [1.0] * 100}
sc.SYMBOLS = ['AAA-USDT', 'BBB-USDT']
sc.fetch_spot_tickers = lambda: None
sc.fetch_candles = lambda sym, bar='1H', limit=100: candle
sc.fetch_ticker = lambda sym: {'price': 1.0, 'change_pct': -3.0}
sc.generate_signal = lambda *a, **k: {'label': 'STRONG BUY', 'score': 4.5, 'reasons': ['RSI 28 — oversold', 'Volume 2.4x']}
sc.reversal_confirmed = lambda *a, **k: True
sc.calc_vol_ratio = lambda v: 2.4
sc._fetch_option3_trades = lambda: []
sc._count_recent_sl = lambda h=24: 0
sc._build_trade_context = lambda cand, regime: {**sc._light_context(cand, regime), 'funding_pct': 0.01}
sc.ai_trade_params = lambda *a, **k: ({'amount_usdt': 120.0, 'partial_tp_pct': 2.5, 'sl_pct': 3.0, 'trailing_pct': 1.2}, None)
placed = []
sc.place_option3_trade = lambda *a, **k: placed.append(a) or {'amount_usdt': 120, 'tp_pct': 2.5, 'sl_pct': 3.0, 'trail_pct': 1.2, 'saved': True}
sc.CLAUDE_API_KEY, sc.OKX_API_KEY = 'k', ''
sc.time.sleep = lambda s: None
paper.flush_scan = lambda *a, **k: None
paper.trend_scan = lambda *a, **k: None


def scan(mode, regime_ok=True):
    sc.STRATEGY_MODE = {'dip': mode, 'flush': 'off', 'trend': 'off'}
    sc.btc_regime_ok = lambda: (regime_ok, 'BTC ok' if regime_ok else 'BTC bearish')
    W.db['paper_trades'] = []; W.db['skipped_setups'] = []; paper._OPEN = None; placed.clear(); W.telegram.clear()
    cache = {'AAA-USDT': {'alerted_zone': 'neutral', 'alerted_at': 0}}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sc.run_scan(cache)
    return cache, buf.getvalue()


cache, log = scan('paper')
ok(not placed and len(W.db['paper_trades']) == 1 and W.db['paper_trades'][0]['strategy'] == 'dip',
   'dip on PAPER: the AI said TRADE, a paper trade was recorded, place_option3_trade was never called')
p = W.db['paper_trades'][0]
ok(p['params'] == {'tp': 2.5, 'sl': 3.0, 'trail': 1.2} and p['amount_usdt'] == 120.0 and p['entry_context']['score'] == 4.5,
   "with the AI's own size and exits, and the entry snapshot")
ok(len(W.db['skipped_setups']) == 1 and W.db['skipped_setups'][0]['gate'] == 'cap',
   'the second coin (ranked below the top pick) is journalled with gate=cap')
ok(len(W.telegram) == 1 and 'PAPER' in W.telegram[0], 'one Telegram message, marked PAPER')
ok(cache['_funnel'] and list(cache['_funnel'].values())[0].get('paper') == 1
   and list(cache['_funnel'].values())[0].get('candidates') == 2, f"funnel counted it: {list(cache['_funnel'].values())[0]}")

cache, log = scan('paper', regime_ok=False)
gates = sorted(r['gate'] for r in W.db['skipped_setups'])
ok(not W.db['paper_trades'] and gates == ['regime', 'regime'],
   'bearish regime: nothing traded, both setups journalled with gate=regime')
ok(all(r['entry_context'].get('suggested') for r in W.db['skipped_setups']),
   'each with the suggested target and stop the 24h grader needs')

sc.OKX_API_KEY = 'k'
sc._fetch_usdt_balance = lambda: 300.0
sc._loss_30d = lambda: 0.0
cache, log = scan('live')
ok(len(placed) == 1 and not W.db['paper_trades'], 'dip on LIVE: the same decision goes to place_option3_trade')
sc._loss_30d = lambda: -25.0
cache, log = scan('live')
ok(not placed and sorted(r['gate'] for r in W.db['skipped_setups']) == ['loss_limit', 'loss_limit']
   and any('monthly loss limit' in t for t in W.telegram),
   'monthly loss limit: -$25 on a $300 account (over 6%) stops new live trades and says so')
cache, log = scan('off')
ok(not placed and not W.db['paper_trades'] and 'OFF' in log, 'dip OFF: signals are noted, nothing is traded or simulated')

# A fault inside the paper module must not stop the scan: it runs in the same loop that
# manages real positions.
real_open, real_open_trade = paper.open_trades, paper.open_trade
def boom(*a, **k): raise RuntimeError('paper module broke')
paper.open_trades = boom
cache, log = scan('paper')
ok('open paper trades unavailable' in log and not placed, 'paper.open_trades() raising: the scan finishes and says so')
paper.open_trades, paper.open_trade = real_open, boom
cache, log = scan('paper')
ok('could not be recorded' in log and not placed and cache['AAA-USDT'].get('alerted_zone') == 'up',
   'paper.open_trade() raising: logged, no order, the scan still completes its bookkeeping')
paper.open_trade = real_open_trade
sc._log_skipped_rows = boom
cache, log = scan('paper', regime_ok=False)
ok('refused-signal log error' in log, 'a journal failure is logged, not raised')

# ── 7. risk cap ───────────────────────────────────────────────────────────────
ok(sc._risk_cap_usdt(300, 3.8) == 78.95 and sc._risk_cap_usdt(300, 7.5) == 40.0,
   '1% risk: a 3.8% stop allows $78.95 of a $300 account, a 7.5% stop $40')
sc.RISK_PER_TRADE_PCT = 0
ok(sc._risk_cap_usdt(300, 3.8) is None, 'and 0 switches the cap off')

print(f'ALL PASSED ({N} checks)')
