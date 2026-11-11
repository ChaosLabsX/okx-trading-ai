r"""
Paper trading: trades on LIVE OKX prices with SIMULATED fills.

Why this exists (2026-11-10). A 34-month test of the original dip-buy rule, and of every
other hourly entry style tried beside it, came out negative after fees (research/
lab_exp1.py; the live record agrees: 46 trades, -0.56% each). So a rule now has to show
it makes money before it is given any. A strategy on paper does everything a live one
does except send orders: its trades are stepped minute by minute against real candles,
charged the same fees plus slippage, stored in `paper_trades`, reported on Telegram
marked PAPER and shown on the dashboard beside the real record.

Which strategies are on paper, and when one moves to real money, is decided by the
strategy ladder in strategies.py (and its rules in docs/STRATEGIES.md). The strategy
rules themselves live there too (flush, trend) or in signal_checker.run_scan (dip).

Real money lives in `option3_trades` and nowhere else. Nothing in this file can place,
change or cancel an order: it never calls an OKX private endpoint.

Exit models:
  option3  the dip strategy's: stop on everything, half sold at the target, stop to
           break-even at 70% of the way, trailing stop on the rest (step_option3)
  hold     buy, sell after N hours; optional far stop (step_hold) - the flush strategy
  rule     held until a daily rule says sell (strategies.trend_scan closes it)
"""
import time
from datetime import datetime, timezone

import requests

import signal_checker as sc

# ── Simulation costs ──────────────────────────────────────────────────────────
# A market order pays the spread and moves the book a little. 0.05% per side is what
# the live stop exits measured (median -0.034%, CHANGELOG 2026-10-19), rounded up.
PAPER_SLIP = 0.0005
STAKE_USDT = 100.0            # notional of a flush / trend paper trade: results read as % and per $100


_OPEN = None                  # open paper trades, loaded once per process and kept current in memory


# ── Supabase ──────────────────────────────────────────────────────────────────
def _on():
    return bool(sc.SUPABASE_URL and sc.SUPABASE_KEY)


def _db(method, path, payload=None, prefer='return=minimal'):
    """One REST call. Returns parsed JSON (or True) on success, None on any failure - a
    paper-trading hiccup must never take the trading loop down with it."""
    try:
        r = requests.request(
            method, f'{sc.SUPABASE_URL}/rest/v1/{path}',
            headers=sc._sb_headers({'Content-Type': 'application/json', 'Prefer': prefer}),
            json=payload, timeout=10)
        if r.status_code not in (200, 201, 204):
            print(f'  [Paper] {method} {path.split("?")[0]} failed: HTTP {r.status_code} {r.text[:160]}')
            return None
        return r.json() if r.text and r.text.strip() else True
    except Exception as e:
        print(f'  [Paper] {method} {path.split("?")[0]} error: {e}')
        return None


def open_trades(strategy=None, refresh=False):
    """Open paper trades. One read per process; opens and closes then update it in memory."""
    global _OPEN
    if not _on():
        return []
    if _OPEN is None or refresh:
        rows = _db('GET', 'paper_trades?closed_at=is.null&order=opened_at.asc'
                          '&select=id,strategy,symbol,kind,opened_at,entry_price,amount_usdt,params,state')
        if rows is None:
            return []                 # table missing or unreachable: behave as "none open", try again next run
        _OPEN = rows if isinstance(rows, list) else []
    return [t for t in _OPEN if strategy is None or t['strategy'] == strategy]


def recent_stop_count(strategy, hours=24):
    """Stop-loss exits of a paper strategy in the last N hours (feeds the dip circuit breaker)."""
    if not _on():
        return 0
    since = datetime.fromtimestamp(time.time() - hours * 3600, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    rows = _db('GET', f'paper_trades?select=id&strategy=eq.{strategy}&exit_reason=eq.sl&closed_at=gte.{since}')
    return len(rows) if isinstance(rows, list) else 0


# ── Fills ─────────────────────────────────────────────────────────────────────
def _net(entry, fill):
    """Net result of selling at `fill`, as a fraction of the stake: price move after exit
    slippage, minus both fees, counted the way signal_checker._exit_pnl counts them.
    `entry` already includes the entry slippage."""
    fill *= (1 - PAPER_SLIP)
    return (fill - entry) / entry - sc.OKX_FEE_RATE * (1 + fill / entry)


def _ms(iso):
    return int(datetime.fromisoformat(iso.replace('Z', '+00:00')).timestamp() * 1000)


def new_state(opened_ms):
    # last_ts = open time of the last candle already processed. The minute the trade was
    # opened in is skipped: part of it happened before the entry.
    return {'phase': 1, 'at_be': False, 'banked': 0.0, 'peak': 0.0,
            'last_ts': opened_ms // 60_000 * 60_000, 'mae': 0.0, 'mfe': 0.0}


def step_option3(entry, params, st, candles):
    """
    Advance one Option 3 paper trade over finished candles [(ts, open, high, low, close)],
    oldest first. Same rules, in the same order, as research/replay.py:simulate(), which is
    what every exit decision in this repo was measured with:
      phase 1  stop on everything (the stop wins a candle that touches both), half sold at
               the target, stop to break-even once price is BREAKEVEN_TRIGGER_FRAC of the way
      phase 2  the other half rides a trailing stop from the target fill
    Mutates `st`; returns None while open, or {'reason', 'price', 'frac', 'ts'} on exit.
    """
    tp, sl, trail = float(params['tp']), float(params['sl']), float(params['trail'])
    sl_px, tp_px = entry * (1 - sl / 100), entry * (1 + tp / 100)
    be_trig = entry * (1 + sc.BREAKEVEN_TRIGGER_FRAC * tp / 100) if sc.BREAKEVEN_TRIGGER_FRAC else None
    be_px = sc._be_price(entry)
    for ts, o, h, l, c in candles:
        st['last_ts'] = ts
        st['mae'] = min(st['mae'], (l / entry - 1) * 100)
        st['mfe'] = max(st['mfe'], (h / entry - 1) * 100)
        if st['phase'] == 1:
            stop = be_px if st['at_be'] else sl_px
            if l <= stop:
                fill = min(o, stop)
                return {'reason': 'be_stop' if st['at_be'] else 'sl', 'price': fill,
                        'frac': _net(entry, fill), 'ts': ts}
            if h >= tp_px:
                fill = max(o, tp_px)
                st['phase'], st['banked'], st['peak'] = 2, _net(entry, fill) * 0.5, fill
                continue
            if be_trig and not st['at_be'] and h >= be_trig:
                st['at_be'] = True                 # takes effect from the next candle
            continue
        floor = st['peak'] * (1 - trail / 100)
        if l <= floor:
            fill = min(o, floor)
            return {'reason': 'tp_trail', 'price': fill,
                    'frac': st['banked'] + _net(entry, fill) * 0.5, 'ts': ts}
        st['peak'] = max(st['peak'], h)
    return None


def step_hold(entry, params, st, candles, opened_ms, bar_ms):
    """Buy-and-hold-for-N-hours trade: sells at the close of the candle that reaches the
    deadline. An optional far stop (`stop_pct`) is checked first on every candle."""
    deadline = opened_ms + float(params['hold_hours']) * 3_600_000
    stop_pct = params.get('stop_pct')
    for ts, o, h, l, c in candles:
        st['last_ts'] = ts
        st['mae'] = min(st['mae'], (l / entry - 1) * 100)
        st['mfe'] = max(st['mfe'], (h / entry - 1) * 100)
        if stop_pct and l <= entry * (1 - stop_pct / 100):
            fill = min(o, entry * (1 - stop_pct / 100))
            return {'reason': 'stop', 'price': fill, 'frac': _net(entry, fill), 'ts': ts}
        if ts + bar_ms >= deadline:
            return {'reason': 'time', 'price': c, 'frac': _net(entry, c), 'ts': ts}
    return None


def candles_since(symbol, since_ms, now_ms=None):
    """
    Finished candles that opened after `since_ms`, oldest first, plus the bar length in ms.
    One-minute candles while the gap is short; coarser ones after an outage, because OKX
    returns at most 300 per call. Coarser bars only make the stop-before-target assumption
    more conservative, they never invent a fill.
    """
    now_ms = now_ms or int(time.time() * 1000)
    gap_min = (now_ms - since_ms) / 60_000
    bar, bar_ms = (('1m', 60_000) if gap_min <= 280 else ('5m', 300_000) if gap_min <= 1400 else
                   ('15m', 900_000) if gap_min <= 4200 else ('1H', 3_600_000))
    r = requests.get(f'{sc.OKX_BASE}/api/v5/market/candles?instId={symbol}&bar={bar}&limit=300', timeout=15)
    r.raise_for_status()
    d = r.json()
    if d.get('code') != '0':
        raise ValueError(f"OKX {d.get('code')}: {d.get('msg', '')}")
    rows = [(int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]))
            for c in d.get('data') or [] if len(c) < 9 or c[8] != '0']
    return sorted(x for x in rows if x[0] > since_ms), bar_ms


# ── Opening and closing ───────────────────────────────────────────────────────
def open_trade(strategy, symbol, price, amount_usdt, kind, params, context=None, plan='', notify=True):
    """
    Record a new paper trade at the current price (plus entry slippage). Returns the row,
    or None if it could not be saved - an unsaved paper trade simply did not happen.
    """
    if not _on() or not price or price <= 0:
        return None
    entry = price * (1 + PAPER_SLIP)
    now_ms = int(time.time() * 1000)
    row = {'strategy': strategy, 'symbol': symbol, 'kind': kind, 'entry_price': entry,
           'amount_usdt': round(float(amount_usdt), 2), 'params': params,
           'state': new_state(now_ms), 'entry_context': context or {}}
    out = _db('POST', 'paper_trades', row, prefer='return=representation')
    if not isinstance(out, list) or not out:
        return None
    saved = out[0]
    if _OPEN is not None:
        _OPEN.append(saved)
    coin = symbol.replace('-USDT', '')
    print(f'  [Paper] {strategy}: opened {symbol} @ {entry:.8g} (${row["amount_usdt"]:.2f}) id={saved["id"]}')
    if not notify:
        return saved
    sc.send_telegram(
        f"📝 <b>PAPER trade opened — {coin}</b>  [{strategy}]\n"
        f"💰 {sc.fmt_price(price)} · stake ${row['amount_usdt']:.2f}\n"
        f"{plan + chr(10) if plan else ''}"
        f"<i>Simulated on live prices. No real money is in this trade.</i>")
    return saved


def close_trade(trade, exit_, st, notify=True):
    """Write the outcome of a paper trade and report it."""
    global _OPEN
    amount = float(trade['amount_usdt'])
    pct = exit_['frac'] * 100
    payload = {
        'closed_at':    datetime.fromtimestamp(exit_.get('ts', time.time() * 1000) / 1000, timezone.utc).isoformat(),
        'exit_price':   exit_['price'], 'exit_reason': exit_['reason'],
        'net_pnl_usdt': round(exit_['frac'] * amount, 4), 'net_pct': round(pct, 3),
        'mae_pct': round(st.get('mae', 0.0), 2), 'mfe_pct': round(st.get('mfe', 0.0), 2), 'state': st,
    }
    if _db('PATCH', f'paper_trades?id=eq.{trade["id"]}', payload) is None:
        return False                               # stays open; the next pass tries again
    if _OPEN is not None:
        _OPEN = [t for t in _OPEN if t['id'] != trade['id']]
    coin = trade['symbol'].replace('-USDT', '')
    label = {'sl': 'stop-loss', 'be_stop': 'break-even stop', 'tp_trail': 'target + trailing stop',
             'time': 'held its full time', 'stop': 'far stop', 'rule': 'trend ended'}.get(exit_['reason'], exit_['reason'])
    print(f'  [Paper] {trade["strategy"]}: closed {trade["symbol"]} {exit_["reason"]} {pct:+.2f}%')
    if not notify:
        return True
    sc.send_telegram(
        f"📝 <b>PAPER trade closed — {coin}</b>  [{trade['strategy']}]\n"
        f"{'🟢' if pct > 0 else '🔴'} Result: {pct:+.2f}% ({sc.fmt_usdt(exit_['frac'] * amount)} on ${amount:.0f}) after fees\n"
        f"📋 Exit: {label}\n"
        f"<i>Simulated. No real money was in this trade.</i>")
    return True


# ── Monitor: step every open paper trade against real candles ─────────────────
def monitor(cache):
    """Called once per scan. One OKX candle request per open paper trade; Supabase is
    written only when a trade closes or changes phase. Minute-level progress lives in the
    local cache file, and is rebuilt from the entry time if that file is ever lost."""
    trades = [t for t in open_trades() if t['kind'] in ('option3', 'hold')]
    store = cache.setdefault('_paper', {})
    if _OPEN is not None:                          # only when the list really loaded: an unreachable
        live_ids = {str(t['id']) for t in _OPEN}   # database must not wipe the progress of open trades
        for k in [k for k in store if k not in live_ids]:
            del store[k]
    if not trades:
        return
    now_ms = int(time.time() * 1000)
    for t in trades:
        try:
            key = str(t['id'])
            opened_ms = _ms(t['opened_at'])
            st = store.get(key) or dict(t.get('state') or {}) or new_state(opened_ms)
            for k_, v_ in new_state(opened_ms).items():
                st.setdefault(k_, v_)
            if st.get('pending_exit'):
                # The trade already ended on an earlier pass but the result could not be
                # saved. The exit is a fact, not something to recompute from later candles.
                if close_trade(t, st['pending_exit'], st):
                    store.pop(key, None)
                continue
            before = (st['phase'], st['at_be'])
            candles, bar_ms = candles_since(t['symbol'], st['last_ts'], now_ms)
            entry = float(t['entry_price'])
            if t['kind'] == 'option3':
                out = step_option3(entry, t['params'], st, candles)
            else:
                out = step_hold(entry, t['params'], st, candles, opened_ms, bar_ms)
            store[key] = st
            if out:
                if close_trade(t, out, st):
                    store.pop(key, None)
                else:
                    st['pending_exit'] = out       # kept in the cache file; retried next pass
            elif (st['phase'], st['at_be']) != before:
                _db('PATCH', f'paper_trades?id=eq.{t["id"]}', {'state': st})
                what = 'target hit, trailing the rest' if st['phase'] == 2 else 'stop moved to break-even'
                print(f'  [Paper] {t["strategy"]}: {t["symbol"]} {what}')
        except Exception as e:
            print(f'  [Paper] monitor error on {t.get("symbol")}: {e}')


# ── Scoreboard ────────────────────────────────────────────────────────────────
def scoreboard():
    """{strategy: {n, wins, avg_pct, pf, open}} over all closed paper trades."""
    rows = _db('GET', 'paper_trades?closed_at=not.is.null&select=strategy,net_pct&order=closed_at.desc&limit=1000')
    out = {}
    for r in rows if isinstance(rows, list) else []:
        s = out.setdefault(r['strategy'], {'n': 0, 'wins': 0, 'sum': 0.0, 'gw': 0.0, 'gl': 0.0, 'open': 0})
        p = float(r.get('net_pct') or 0)
        s['n'] += 1; s['wins'] += p > 0; s['sum'] += p
        s['gw'] += max(p, 0); s['gl'] += max(-p, 0)
    for t in open_trades():
        out.setdefault(t['strategy'], {'n': 0, 'wins': 0, 'sum': 0.0, 'gw': 0.0, 'gl': 0.0, 'open': 0})['open'] += 1
    for s in out.values():
        s['avg_pct'] = s['sum'] / s['n'] if s['n'] else 0.0
        s['pf'] = s['gw'] / s['gl'] if s['gl'] > 0 else None
    return out
