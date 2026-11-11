r"""
Strategies: which of them trade real money, how much, and the rules of the two that are
not the dip rule (flush, trend).

Every strategy sits on one rung of a ladder, recomputed from its own record every run:

    paper   no real money: same signals and exits, fills simulated (paper.py)
    trial   real money, TRIAL_STAKE_USDT a trade
    full    real money at full size: FULL_STAKE_PCT of the account a trade
            (dip: the AI's own sizing, under the 1%-risk cap and the 45-60% ladder)

    up      paper -> trial   when its record passes the bar (below)
            trial -> full    when it still passes with at least FULL_MIN_LIVE real trades in it
    down    -> paper         when it has lost money on average over MIN_TRADES or more trades,
                             or its real trades lost more than LOSS_BUDGET_PCT of the account
                             in 30 days

The bar (set 2026-11-10, before any result existed; docs/STRATEGIES.md):
    MIN_TRADES or more closed trades, average result above zero after costs, profit
    factor at least MIN_PF, and the average clears SE_MULT standard errors - computed with
    the trades opened on the same day averaged into one, because a market-wide crash
    moves every coin together and thirty trades from three days are not thirty
    independent results.

The record is every closed trade of that strategy since EVIDENCE_SINCE, real and paper
together: they are the same rule with the same exits and costs. Trades before that date
do not count - the dip strategy's 46 live trades failed the bar and are not carried.

The owner can override a strategy in signal_checker.STRATEGY_MODE: 'auto' (this ladder,
from STRATEGY_START), 'paper', 'off', or 'live' (full size, no automatic demotion).

The level changes announce themselves on Telegram. Nothing here places an order directly:
real orders go through signal_checker (place_simple_trade, place_option3_trade).
"""
import math
import time
from datetime import datetime, timezone, timedelta

import requests

import signal_checker as sc
import paper

# ── The ladder ───────────────────────────────────────────────────────────────
TRIAL_STAKE_USDT = 20.0
FULL_STAKE_PCT   = {'flush': 10.0, 'trend': 8.0}   # of the whole account, per trade
LOSS_BUDGET_PCT  = 5.0
EVIDENCE_SINCE   = '2026-11-10T00:00:00Z'
MIN_TRADES       = 30
MIN_PF           = 1.3
SE_MULT          = 1.3
FULL_MIN_LIVE    = 10
LIVE_MAX_OPEN    = {'flush': 4, 'trend': 12}
LEVEL_TTL_SECS   = 5 * 60

# ── flush: buy forced selling ────────────────────────────────────────────────
# A coin down FLUSH_FALL_PCT over 24h while its futures open interest, counted in coins,
# fell FLUSH_OI_DROP_PCT: leveraged longs being liquidated, and forced selling ends. Buy,
# hold FLUSH_HOLD_HOURS, sell. Protective stop at signal_checker.DISASTER_STOP_PCT, on
# paper too, so paper and real trades are the same trade. Evidence: research/lab_exp6.txt,
# lab_exp7.txt (+3.2% a trade with that stop on 2024-26; +1.0% on 2022-23, not significant).
FLUSH_FALL_PCT    = 8.0
FLUSH_OI_DROP_PCT = 15.0
FLUSH_HOLD_HOURS  = 48
FLUSH_COOLDOWN_H  = 72        # one flush trade per coin per this many hours
FLUSH_PAPER_MAX   = 4         # paper positions on top of the real ones (signals the real cap turned away)
FLUSH_OI_LOOKUPS  = 10        # open-interest lookups per scan (OKX allows 5 per 2s on that endpoint)

# ── trend: the largest coins, held while they trend up ──────────────────────
TREND_COINS = ['BTC', 'ETH', 'BNB', 'XRP', 'ADA', 'DOGE', 'SOL', 'LTC', 'TRX', 'DOT', 'AVAX', 'LINK']
TREND_EMA   = 50
TREND_BAND  = 0.02

PAPER_STAKE_USDT = paper.STAKE_USDT

_CACHE = {'at': 0.0, 'equity': None, 'levels': {}}


# ── Evidence and levels ──────────────────────────────────────────────────────
def _get(path):
    r = requests.get(f'{sc.SUPABASE_URL}/rest/v1/{path}', headers=sc._sb_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def _day(iso):
    return str(iso or '')[:10]


def fetch_records():
    """strategy -> [{'day', 'pct', 'live', 'usd', 'closed'}] for every closed trade since
    EVIDENCE_SINCE, real and paper. Two requests. Raises if the database cannot be read."""
    out = {}
    live = _get(f'option3_trades?phase=eq.3&closed_at=gte.{EVIDENCE_SINCE}&net_pnl_usdt=not.is.null'
                f'&select=strategy,amount_usdt,net_pnl_usdt,created_at,closed_at')
    for t in live:
        cost = float(t.get('amount_usdt') or 0)
        if cost <= 0:
            continue
        usd = float(t.get('net_pnl_usdt') or 0)
        out.setdefault(t.get('strategy') or 'dip', []).append(
            {'day': _day(t.get('created_at')), 'pct': usd / cost * 100, 'live': True, 'usd': usd,
             'closed': t.get('closed_at')})
    pap = _get(f'paper_trades?closed_at=gte.{EVIDENCE_SINCE}&select=strategy,net_pct,net_pnl_usdt,opened_at,closed_at')
    for t in pap:
        out.setdefault(t['strategy'], []).append(
            {'day': _day(t.get('opened_at')), 'pct': float(t.get('net_pct') or 0), 'live': False,
             'usd': float(t.get('net_pnl_usdt') or 0), 'closed': t.get('closed_at')})
    return out


def stats(trades, now=None):
    """The numbers the ladder reads, for one strategy's trades."""
    now = now or datetime.now(timezone.utc)
    n = len(trades)
    pcts = [t['pct'] for t in trades]
    mean = sum(pcts) / n if n else 0.0
    gw = sum(p for p in pcts if p > 0)
    gl = -sum(p for p in pcts if p < 0)
    pf = gw / gl if gl > 0 else (math.inf if gw > 0 else None)
    by_day = {}
    for t in trades:
        by_day.setdefault(t['day'], []).append(t['pct'])
    days = [sum(v) / len(v) for v in by_day.values()]
    # The "not luck" test runs on DAILY averages: 20 winners on one crash day are one
    # result, not twenty. day_mean is the average of those daily averages; se is its
    # standard error. (mean, the plain average per trade, is what the owner reads.)
    day_mean = sum(days) / len(days) if days else 0.0
    se = None
    if len(days) >= 2:
        se = math.sqrt(sum((d - day_mean) ** 2 for d in days) / (len(days) - 1) / len(days))
    cutoff = (now - timedelta(days=30)).isoformat()
    live = [t for t in trades if t['live']]
    return {
        'n': n, 'wins': sum(1 for p in pcts if p > 0), 'mean': mean, 'pf': pf, 'se': se,
        'day_mean': day_mean, 'days': len(days), 'n_live': len(live), 'n_paper': n - len(live),
        'live_usd': sum(t['usd'] for t in live),
        'live_usd_30d': sum(t['usd'] for t in live if str(t['closed'] or '') >= cutoff[:19]),
    }


def decide(name, st, equity, mode=None, start=None):
    """The ladder, as a pure function: (level, reason)."""
    mode = mode or sc.STRATEGY_MODE.get(name, 'off')
    if mode in ('off', 'paper'):
        return mode, 'set by hand in STRATEGY_MODE'
    if mode == 'live':
        return 'full', 'set to live by hand in STRATEGY_MODE (no automatic demotion)'
    start = start or sc.STRATEGY_START.get(name, 'paper')
    se_ok = st['se'] is not None and st['day_mean'] - SE_MULT * st['se'] > 0
    passes = (st['n'] >= MIN_TRADES and st['mean'] > 0 and st['pf'] is not None
              and st['pf'] >= MIN_PF and se_ok)
    if st['n'] >= MIN_TRADES and st['mean'] <= 0:
        return 'paper', f"lost money on average over {st['n']} trades ({st['mean']:+.2f}% a trade)"
    if equity and equity > 0 and st['live_usd_30d'] <= -equity * LOSS_BUDGET_PCT / 100:
        return 'paper', (f"real trades lost ${-st['live_usd_30d']:.2f} in 30 days, over the "
                         f"{LOSS_BUDGET_PCT:g}% budget")
    if passes and st['n_live'] >= FULL_MIN_LIVE:
        return 'full', f"passed the bar over {st['n']} trades, {st['n_live']} of them real"
    if passes:
        return 'trial', f"passed the bar over {st['n']} trades — now tested with real money"
    if start == 'trial':
        return 'trial', 'started on a trial by the owner\'s choice (2026-11-11)'
    return 'paper', f"needs {MIN_TRADES}+ trades that pass the bar (has {st['n']})"


def stake_for(name, level, equity):
    """USDT per trade for a live level (None for dip at full size: the AI sizes it)."""
    if level == 'trial':
        return TRIAL_STAKE_USDT
    if level == 'full':
        pct = FULL_STAKE_PCT.get(name)
        if pct is None:
            return None
        return round(max(TRIAL_STAKE_USDT, (equity or 0) * pct / 100), 2)
    return 0.0


def levels(equity=None, refresh=False):
    """name -> {'level', 'reason', 'stake', 'stats'} for every strategy. Cached for
    LEVEL_TTL_SECS; on a database error the last answer stands (or, with none, every
    strategy is treated as paper - never live on missing evidence)."""
    if equity:
        _CACHE['equity'] = equity
    equity = _CACHE['equity'] or sc.PAPER_BALANCE_USDT
    if not refresh and _CACHE['levels'] and time.time() - _CACHE['at'] < LEVEL_TTL_SECS:
        return _CACHE['levels']
    try:
        recs = fetch_records() if sc.SUPABASE_URL and sc.SUPABASE_KEY else {}
    except Exception as e:
        print(f'  [Levels] record unavailable ({e}) — keeping the last levels')
        if _CACHE['levels']:
            return _CACHE['levels']
        return {k: {'level': 'paper', 'reason': 'record unavailable', 'stake': 0.0, 'stats': stats([])}
                for k in sc.STRATEGY_MODE}
    out = {}
    for name in sc.STRATEGY_MODE:
        st = stats(recs.get(name, []))
        lvl, why = decide(name, st, equity)
        out[name] = {'level': lvl, 'reason': why, 'stake': stake_for(name, lvl, equity), 'stats': st}
    _CACHE.update(at=time.time(), levels=out)
    return out


def level(name, equity=None):
    return levels(equity).get(name) or {'level': 'off', 'reason': 'unknown strategy', 'stake': 0.0, 'stats': stats([])}


def is_live(name, equity=None):
    return level(name, equity)['level'] in ('trial', 'full')


def announce(cache, equity=None):
    """Telegram when a strategy changes rung; on the very first run, one message with the
    whole line-up so the owner sees what the deploy switched on."""
    cur = {k: v['level'] for k, v in levels(equity).items()}
    prev = cache.get('_levels')
    cache['_levels'] = cur
    if prev is None:
        lines = [f"• {sc.STRATEGY_LABEL.get(k, k)}: {_level_text(k, v['level'], v['stake'])}"
                 for k, v in levels(equity).items()]
        sc.send_telegram('⚙️ <b>Strategy line-up</b>\n' + '\n'.join(lines) +
                         '\n<i>Each one moves up or down by itself from its own results.</i>')
        return
    for k, lvl in cur.items():
        if prev.get(k) == lvl:
            continue
        info = levels(equity)[k]
        st, name = info['stats'], sc.STRATEGY_LABEL.get(k, k)
        record = (f"{st['n']} trades ({st['n_live']} real), {st['mean']:+.2f}% a trade on average"
                  if st['n'] else 'no trades yet')
        up = LEVELS_ORDER.index(lvl) > LEVELS_ORDER.index(prev.get(k, 'paper'))
        sc.send_telegram(
            f"{'📈' if up else '📉'} <b>{name}: {prev.get(k, '?')} → {lvl}</b>\n"
            f"{'From now on it ' + _level_text(k, lvl, info['stake']) if lvl in ('trial', 'full') else 'No more real trades until it earns them again.'}\n"
            f"📋 {info['reason']}\n📊 Record since {EVIDENCE_SINCE[:10]}: {record}")


LEVELS_ORDER = ['off', 'paper', 'trial', 'full']


def _level_text(name, lvl, stake):
    if lvl == 'trial':
        return f'trades REAL money, ${stake:g} a trade (trial)'
    if lvl == 'full':
        return 'trades REAL money at full size' + (f' (${stake:.0f} a trade)' if stake else ' (sized by the AI)')
    return {'paper': 'paper only (no real money)', 'off': 'off'}.get(lvl, lvl)


# ── Live-or-paper routing for the simple strategies ──────────────────────────
def _live_slot(name, symbol, live_rows, equity):
    """None if a REAL trade may be opened now, else the reason it may not."""
    info = level(name, equity)
    if info['level'] not in ('trial', 'full'):
        return f"{info['level']}: {info['reason']}"
    if not (sc.OKX_API_KEY and sc.OKX_SECRET_KEY and sc.OKX_PASSPHRASE):
        return 'no OKX key on this machine'
    if sum(1 for t in live_rows if t.get('strategy') == name) >= LIVE_MAX_OPEN.get(name, 1):
        return f'{LIVE_MAX_OPEN.get(name, 1)} real {name} positions already open'
    if any(t.get('symbol') == symbol for t in live_rows):
        return f'{symbol} already held by a real trade'
    paused = sc.live_paused(equity)
    if paused:
        return paused
    return None


def _open(name, symbol, price, live_rows, equity, exit_plan, paper_params, ctx, why, paper_cap=None):
    """Open a real trade when the ladder and the limits allow it, else a paper one.
    Returns 'live', 'paper' or None (nothing opened)."""
    blocked = _live_slot(name, symbol, live_rows, equity)
    if blocked is None:
        info = level(name, equity)
        try:
            row = sc.place_simple_trade(name, symbol, info['stake'], exit_plan, ctx, info['level'], why)
            live_rows.append(row)
            return 'live'
        except sc.Option3Preflight as e:
            print(f'  [{name}] {symbol}: real trade not possible ({e}) — paper instead')
        except Exception as e:
            print(f'  [{name}] {symbol}: real trade failed ({e})')
            return None                        # coins were sold back and the owner told; no paper twin
    elif not blocked.startswith('paper') and not blocked.startswith('off'):
        print(f'  [{name}] {symbol}: no real trade ({blocked}) — paper instead')
    if paper_cap is not None and len(paper.open_trades(name)) >= paper_cap:
        return None
    return 'paper' if paper.open_trade(name, symbol, price, PAPER_STAKE_USDT, paper_params['kind'],
                                       paper_params['params'], ctx, plan=why,
                                       notify=paper_params.get('notify', True)) else None


# ── flush ─────────────────────────────────────────────────────────────────────
def oi_change_24h(coin, price_chg_pct):
    """
    24h change of a coin's futures open interest on OKX, in COINS, as a percentage.
    OKX reports it in dollars (all contracts on that coin, hourly); dollars fall with the
    price even when nobody closed a contract, so the price move is divided back out.
    None when OKX has no data for the coin.
    """
    r = requests.get(f'{sc.OKX_BASE}/api/v5/rubik/stat/contracts/open-interest-volume?ccy={coin}&period=1H', timeout=10)
    r.raise_for_status()
    rows = r.json().get('data') or []
    if len(rows) < 25:
        return None
    now_usd, then_usd = float(rows[0][1]), float(rows[24][1])
    if now_usd <= 0 or then_usd <= 0:
        return None
    return ((now_usd / then_usd) / (1 + price_chg_pct / 100) - 1) * 100


def _chg(t):
    return round((t['last'] / t['open24h'] - 1) * 100, 2) if t and t.get('open24h') else None


def flush_scan(tickers, cache, equity=None, live_rows=None):
    """
    `tickers`: instId -> {'last', 'open24h', 'vol'} for every OKX spot pair (one call the
    scan already makes). Only coins already down FLUSH_FALL_PCT are looked up for open
    interest, each at most once an hour, since that figure only changes hourly.
    """
    if sc.STRATEGY_MODE.get('flush', 'off') == 'off' or not tickers or not paper._on():
        return
    live_rows = live_rows if live_rows is not None else []
    now = time.time()
    mem = cache.setdefault('_flush', {})
    held = {t['symbol'] for t in paper.open_trades('flush')} | \
           {t['symbol'] for t in live_rows if t.get('strategy') == 'flush'}
    fallers = []
    for sym in sc.SYMBOLS:
        t = tickers.get(sym)
        if not t or not t.get('open24h') or t.get('vol', 0) < sc.MIN_OKX_VOL_24H_USDT:
            continue
        chg = (t['last'] / t['open24h'] - 1) * 100
        if chg <= -FLUSH_FALL_PCT:
            fallers.append((chg, sym, t['last']))
    lookups, hour = 0, int(now // 3600)
    for chg, sym, last in sorted(fallers):                       # hardest hit first
        if lookups >= FLUSH_OI_LOOKUPS:
            break
        m = mem.setdefault(sym, {})
        if sym in held or now - m.get('opened', 0) < FLUSH_COOLDOWN_H * 3600 or m.get('hour') == hour:
            continue
        m['hour'] = hour
        lookups += 1
        try:
            oi = oi_change_24h(sym.replace('-USDT', ''), chg)
        except Exception as e:
            print(f'  [Flush] {sym}: open-interest lookup failed: {e}')
            continue
        time.sleep(0.45)
        if oi is None:
            print(f'  [Flush] {sym}: {chg:+.1f}% in 24h, no open-interest data')
            continue
        print(f'  [Flush] {sym}: {chg:+.1f}% in 24h, open interest {oi:+.1f}% '
              f'({"FLUSH" if oi <= -FLUSH_OI_DROP_PCT else "not a flush"})')
        if oi > -FLUSH_OI_DROP_PCT:
            continue
        ctx = {'chg_24h': round(chg, 2), 'oi_chg_24h': round(oi, 2), 'fallers': len(fallers),
               'btc_chg_24h': _chg(tickers.get('BTC-USDT'))}
        why = f'📉 {chg:+.1f}% in 24h with open interest {oi:+.1f}% (forced selling)'
        got = _open('flush', sym, last, live_rows, equity,
                    {'kind': 'hold', 'hold_hours': FLUSH_HOLD_HOURS},
                    {'kind': 'hold', 'params': {'hold_hours': FLUSH_HOLD_HOURS, 'stop_pct': sc.DISASTER_STOP_PCT}},
                    ctx, why + f'\n⏱ Plan: hold {FLUSH_HOLD_HOURS}h, then sell. Protective stop −{sc.DISASTER_STOP_PCT:g}%.',
                    paper_cap=FLUSH_PAPER_MAX)
        if got:
            m['opened'] = now
            held.add(sym)


# ── trend ─────────────────────────────────────────────────────────────────────
def trend_scan(cache, equity=None, live_rows=None):
    """Once per UTC day, a few minutes after the daily candle closes: for each coin, in
    above the upper band, out below the lower one. Decided on FINISHED daily candles."""
    if sc.STRATEGY_MODE.get('trend', 'off') == 'off' or not paper._on():
        return
    now = datetime.now(timezone.utc)
    if now.hour == 0 and now.minute < 5:
        return
    day = now.strftime('%Y-%m-%d')
    mem = cache.setdefault('_trend', {})
    if mem.get('day') == day:
        return
    live_rows = live_rows if live_rows is not None else []
    held_paper = {t['symbol']: t for t in paper.open_trades('trend')}
    held_live = {t['symbol']: t for t in live_rows if t.get('strategy') == 'trend'}
    done, bought, sold = True, [], []
    for coin in TREND_COINS:
        sym = f'{coin}-USDT'
        try:
            c = sc.fetch_candles(sym, bar='1Dutc', limit=299)
            if not c or len(c['closes']) < 150:
                continue
            px, ema = c['closes'][-1], sc.ema_array(c['closes'], TREND_EMA)[-1]
            gap = (px / ema - 1) * 100
            if sym not in held_paper and sym not in held_live and px > ema * (1 + TREND_BAND):
                tk = sc.fetch_ticker(sym)
                got = _open('trend', sym, tk['price'] if tk else px, live_rows, equity,
                            {'kind': 'rule', 'ema': TREND_EMA, 'band': TREND_BAND},
                            {'kind': 'rule', 'params': {'ema': TREND_EMA, 'band': TREND_BAND}, 'notify': False},
                            {'gap_pct': round(gap, 2)},
                            f'📈 Daily close {gap:+.1f}% above its {TREND_EMA}-day average')
                if got:
                    bought.append(f"{coin}{' (REAL)' if got == 'live' else ''}")
            elif px < ema * (1 - TREND_BAND):
                if sym in held_live:
                    if sc.close_simple_trade(held_live[sym], 'rule', cache):
                        sold.append(f'{coin} (REAL)')
                    else:
                        done = False                   # try again on the next run
                elif sym in held_paper:
                    t = held_paper[sym]
                    tk = sc.fetch_ticker(sym)
                    out_px = tk['price'] if tk else px
                    frac = paper._net(float(t['entry_price']), out_px)
                    if paper.close_trade(t, {'reason': 'rule', 'price': out_px, 'frac': frac, 'ts': time.time() * 1000},
                                         dict(t.get('state') or {}), notify=False):
                        sold.append(f'{coin} {frac * 100:+.2f}%')
            time.sleep(0.2)
        except Exception as e:
            done = False                               # try this coin again on the next run today
            print(f'  [Trend] {sym}: {e}')
    if bought or sold:
        n_held = len(paper.open_trades('trend')) + sum(1 for t in live_rows if t.get('strategy') == 'trend')
        real = any('(REAL)' in x for x in bought + sold)
        sc.send_telegram(
            '📝 <b>Trend strategy — daily check</b>\n'
            + (f"📈 Bought: {', '.join(bought)}\n" if bought else '')
            + (f"📉 Sold (trend ended): {', '.join(sold)}\n" if sold else '')
            + f'Holding {n_held} of {len(TREND_COINS)}. '
            + ('<i>Coins marked (REAL) use real money; the rest are paper.</i>' if real
               else '<i>Paper trades: no real money involved.</i>'))
    if done:
        mem['day'] = day
        print(f'  [Trend] daily check done')


# ── Reporting ────────────────────────────────────────────────────────────────
def status():
    """For the bot_status row: every strategy's level, stake, reason and record."""
    out = {}
    for k, v in levels().items():
        st = v['stats']
        out[k] = {'level': v['level'], 'reason': v['reason'], 'stake': v['stake'],
                  'n': st['n'], 'wins': st['wins'], 'n_live': st['n_live'], 'n_paper': st['n_paper'],
                  'days': st['days'], 'mean': round(st['mean'], 3), 'day_mean': round(st['day_mean'], 3),
                  'pf': None if st['pf'] is None else (999.0 if st['pf'] == math.inf else round(st['pf'], 3)),
                  'se': None if st['se'] is None else round(st['se'], 3),
                  'live_usd': round(st['live_usd'], 2), 'live_usd_30d': round(st['live_usd_30d'], 2)}
    return {'since': EVIDENCE_SINCE, 'trial_stake': TRIAL_STAKE_USDT, 'loss_budget_pct': LOSS_BUDGET_PCT,
            'bar': {'min_trades': MIN_TRADES, 'min_pf': MIN_PF, 'se_mult': SE_MULT, 'full_min_live': FULL_MIN_LIVE},
            'strategies': out}


def digest_lines():
    """One line per strategy for the daily Telegram report."""
    lines = []
    for k, v in levels().items():
        st = v['stats']
        rec = (f"{st['n']} trades since {EVIDENCE_SINCE[5:10].replace('-', '/')} ({st['n_live']} real), "
               f"{st['mean']:+.2f}% avg" if st['n'] else 'no trades yet')
        lines.append(f"• {sc.STRATEGY_LABEL.get(k, k)}: {_level_text(k, v['level'], v['stake'])} · {rec}")
    return lines
