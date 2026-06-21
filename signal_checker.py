"""
TradingAI — Background Signal Checker (24/7 mode)
Runs every 5 minutes on GitHub Actions.
Each run loops internally for ~4 minutes (one scan every 60 s).

Alert rules:
- BUY and STRONG BUY are the same zone — oscillating between them produces NO extra alert.
- SELL and STRONG SELL are the same zone — same rule.
- A new alert fires only when the zone CHANGES (entering BUY zone, flipping to SELL, etc.).
- 2-minute safety cooldown prevents false alerts from rapid back-and-forth oscillation.
- SELL/STRONG SELL alerts fire for all watched coins (not filtered by portfolio).
"""

import base64
import hashlib
import hmac
import json
import math
import os
import time
from datetime import datetime, timezone

import requests

# ── Config ────────────────────────────────────────────────────────────────────
SYMBOLS = [
    'BTC-USDT',  'ETH-USDT',  'XRP-USDT',  'ADA-USDT',
    'AVAX-USDT', 'SOL-USDT',  'DOGE-USDT', 'PEPE-USDT', 'WIF-USDT',
    'SUI-USDT',  'NEAR-USDT', 'INJ-USDT',  'APT-USDT',  'FET-USDT',
    'TIA-USDT',  'LINK-USDT', 'SEI-USDT',  'OP-USDT',   'ARB-USDT',
    'DOT-USDT',  'ATOM-USDT', 'RUNE-USDT', 'JUP-USDT',  'BONK-USDT',
    'FLOKI-USDT',
    'TON-USDT',  'TRX-USDT',  'HBAR-USDT', 'ENA-USDT',
    'STRK-USDT', 'ONDO-USDT', 'POL-USDT',  'LDO-USDT',
]

OKX_BASE           = 'https://www.okx.com'
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID', '')
OKX_API_KEY        = os.environ.get('OKX_API_KEY', '')
OKX_SECRET_KEY     = os.environ.get('OKX_SECRET_KEY', '')
OKX_PASSPHRASE     = os.environ.get('OKX_PASSPHRASE', '')
CACHE_FILE         = 'signal_cache.json'

SUPABASE_URL = 'https://trbfhtopkcupzeqmrnom.supabase.co'
SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRyYmZodG9wa2N1cHplcW1ybm9tIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODExNDI1NDYsImV4cCI6MjA5NjcxODU0Nn0.6XKKIJIotc4lRVL_akt7P63woJiB8NyOVaUotQmmpHQ'

LOOP_DURATION  = 4 * 60   # seconds per GitHub Actions run
CHECK_INTERVAL = 60        # seconds between scans

SELL_LABELS  = {'SELL', 'STRONG SELL'}
EMOJI        = {'STRONG BUY': '🟢', 'BUY': '🔵', 'SELL': '🟠', 'STRONG SELL': '🔴'}

# Minimum seconds between any two alerts for the same coin.
# A genuine zone flip (BUY→SELL) within this window is suppressed — if a signal
# flips zones twice in 2 minutes it is noise, not a real signal.
FLIP_COOLDOWN = 2 * 60

# If a coin stays in the same BUY/SELL zone longer than this, send a reminder alert.
REZONE_REMINDER = 4 * 60 * 60  # 4 hours

# ── Signal pipeline test ───────────────────────────────────────────────────────
# Set to True to force a STRONG BUY alert for BTC on the next run — bypasses all
# market logic so you can confirm GitHub Actions → Telegram is working end-to-end.
# Delete the cache on GitHub (Actions → Caches) before running, then set back to False.
TEST_FORCE_SIGNAL = False

# ── Zone helpers ──────────────────────────────────────────────────────────────
def direction_zone(label):
    """Collapse fine-grained labels into broad zones for dedup purposes."""
    if label in ('STRONG BUY', 'BUY'):   return 'up'
    if label in ('STRONG SELL', 'SELL'): return 'down'
    return 'neutral'


# ── OKX data fetching ─────────────────────────────────────────────────────────
def fetch_candles(symbol, bar='1H', limit=100):
    url = f'{OKX_BASE}/api/v5/market/candles?instId={symbol}&bar={bar}&limit={limit}'
    r   = requests.get(url, timeout=15)
    r.raise_for_status()
    d   = r.json()
    if d['code'] != '0' or not d.get('data'):
        return None
    rows = list(reversed(d['data']))
    return {
        'opens':   [float(c[1]) for c in rows],
        'closes':  [float(c[4]) for c in rows],
        'volumes': [float(c[5]) for c in rows],
    }


def fetch_ticker(symbol):
    url = f'{OKX_BASE}/api/v5/market/ticker?instId={symbol}'
    r   = requests.get(url, timeout=15)
    r.raise_for_status()
    d   = r.json()
    if d['code'] != '0' or not d.get('data'):
        return None
    t = d['data'][0]
    last, open24 = float(t['last']), float(t['open24h'])
    return {'price': last, 'change_pct': (last - open24) / open24 * 100 if open24 else 0}


# ── Technical indicators ──────────────────────────────────────────────────────
def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    avg_gain = avg_loss = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d > 0: avg_gain += d
        else:     avg_loss -= d
    avg_gain /= period
    avg_loss /= period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period
    return 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)


def ema_array(vals, period):
    k, out = 2 / (period + 1), [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def calc_macd(closes):
    if len(closes) < 35:
        return None
    ema12, ema26 = ema_array(closes, 12), ema_array(closes, 26)
    ml = [a - b for a, b in zip(ema12[25:], ema26[25:])]
    sl = ema_array(ml, 9)
    n  = len(ml) - 1
    return {
        'trend':         'bullish' if ml[n] > sl[n] else 'bearish',
        'bullish_cross': n > 0 and ml[n - 1] < sl[n - 1] and ml[n] >= sl[n],
        'bearish_cross': n > 0 and ml[n - 1] > sl[n - 1] and ml[n] <= sl[n],
    }


def calc_bb(closes, period=20):
    if len(closes) < period:
        return None
    sl   = closes[-period:]
    mean = sum(sl) / period
    std  = math.sqrt(sum((x - mean) ** 2 for x in sl) / period)
    upper, lower = mean + 2 * std, mean - 2 * std
    return {'pct_b': (closes[-1] - lower) / (upper - lower) if upper > lower else 0.5}


def calc_vol_ratio(volumes):
    """Current bar volume relative to the prior 20-bar average."""
    if len(volumes) < 21:
        return None
    avg = sum(volumes[-21:-1]) / 20
    return volumes[-1] / avg if avg > 0 else None


def reversal_confirmed(opens, closes, volumes, zone):
    """
    Guards against alerting into a falling knife (BUY) or a rising knife (SELL).

    Uses 30min candle data (passed from run_scan) for earlier entry detection.
    Falls back gracefully if 30min data is unavailable.

    Requires ALL three conditions on the most recent candle:
      BUY  → candle green (close ≥ open)  AND RSI turning up   AND volume ≥ 1.0× avg
      SELL → candle red   (close ≤ open)  AND RSI turning down AND volume ≥ 1.0× avg

    Returns True  = allow the alert
            False = suppress — no reversal evidence yet
    """
    if len(closes) < 16 or len(opens) < 2:
        return True  # not enough history — don't filter

    rsi_now  = calc_rsi(closes)
    rsi_prev = calc_rsi(closes[:-1])
    if rsi_now is None or rsi_prev is None:
        return True

    # Volume confirmation: last candle must be at least 1.0× the 20-bar average
    vol_ok = True
    if volumes and len(volumes) >= 21:
        avg_vol = sum(volumes[-21:-1]) / 20
        vol_ok  = avg_vol > 0 and volumes[-1] >= avg_vol * 1.0

    if zone == 'up':    # BUY signal
        return closes[-1] >= opens[-1] and rsi_now >= rsi_prev and vol_ok
    if zone == 'down':  # SELL signal
        return closes[-1] <= opens[-1] and rsi_now <= rsi_prev and vol_ok
    return True


def generate_signal(rsi, macd, bb, vol_ratio=None):
    score, reasons = 0.0, []

    if rsi is not None:
        if   rsi <= 20: score += 3; reasons.append(f'RSI {rsi:.0f} — extremely oversold')
        elif rsi <= 30: score += 2; reasons.append(f'RSI {rsi:.0f} — oversold')
        elif rsi <= 40: score += 1; reasons.append(f'RSI {rsi:.0f} — below neutral')
        elif rsi >= 80: score -= 3; reasons.append(f'RSI {rsi:.0f} — extremely overbought')
        elif rsi >= 70: score -= 2; reasons.append(f'RSI {rsi:.0f} — overbought')
        elif rsi >= 60: score -= 1; reasons.append(f'RSI {rsi:.0f} — above neutral')

    if macd is not None:
        if   macd['bullish_cross']: score += 2; reasons.append('MACD bullish crossover')
        elif macd['bearish_cross']: score -= 2; reasons.append('MACD bearish crossover')
        elif macd['trend'] == 'bullish': score += 0.5
        else:                            score -= 0.5

    if bb is not None:
        if   bb['pct_b'] <= 0.05: score += 2; reasons.append('Price at lower Bollinger Band')
        elif bb['pct_b'] <= 0.20: score += 1; reasons.append('Price near lower BB')
        elif bb['pct_b'] >= 0.95: score -= 2; reasons.append('Price at upper Bollinger Band')
        elif bb['pct_b'] >= 0.80: score -= 1; reasons.append('Price near upper BB')

    if vol_ratio is not None:
        if   vol_ratio >= 2.0: score += 1; reasons.append(f'Volume {vol_ratio:.1f}× avg — strong buying interest')
        elif vol_ratio >= 1.5:             reasons.append(f'Volume {vol_ratio:.1f}× avg — elevated')

    label = ('STRONG BUY'  if score >= 4  else
             'BUY'         if score >= 2  else
             'STRONG SELL' if score <= -4 else
             'SELL'        if score <= -2 else 'HOLD')
    return {'label': label, 'score': score, 'reasons': reasons}


# ── Helpers ───────────────────────────────────────────────────────────────────
def fmt_price(p):
    return f'${p:,.2f}' if p >= 10000 else f'${p:.4f}' if p >= 1 else f'${p:.6f}'


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print('  [Telegram] credentials not set — skipping.')
        return
    r = requests.post(
        f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
        json={'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'HTML'},
        timeout=15,
    )
    print(f'  [Telegram] {"sent OK" if r.status_code == 200 else f"error {r.status_code}"}')


def format_alert(symbol, sig, ticker):
    coin    = symbol.replace('-USDT', '')
    emoji   = EMOJI.get(sig['label'], '⚪')
    reasons = ' · '.join(sig['reasons']) if sig['reasons'] else 'Multiple indicators aligned'
    ts      = time.strftime('%H:%M UTC')
    score   = sig['score']
    return (
        f"{emoji} <b>{sig['label']}: {coin}</b>  [{score:+.1f}]\n"
        f"💰 {fmt_price(ticker['price'])} ({ticker['change_pct']:+.2f}% 24h)\n"
        f"📊 {reasons}\n"
        f"⏰ {ts}"
    )


def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                data = json.load(f)
            # Migrate old format {symbol: label_str} → {symbol: dict}
            result = {}
            for k, v in data.items():
                if isinstance(v, str):
                    old_zone = direction_zone(v)
                    result[k] = {
                        'label':        v,
                        'alerted_zone': old_zone,
                        'alerted_at':   time.time() - FLIP_COOLDOWN,
                    }
                else:
                    result[k] = v
            return result
        except Exception:
            pass
    return {}


def save_cache(data):
    with open(CACHE_FILE, 'w') as f:
        json.dump(data, f, indent=2)


# ── Single scan ───────────────────────────────────────────────────────────────
def run_scan(cache):
    now = time.time()

    # Fetch active trades once so every coin check is O(1) — no per-coin Supabase call
    active_trades   = _fetch_option3_trades()
    active_symbols  = {t['symbol'] for t in active_trades}

    for symbol in SYMBOLS:
        try:
            candle_data = fetch_candles(symbol)
            ticker      = fetch_ticker(symbol)
            if not candle_data or not ticker:
                print(f'  {symbol}: no data')
                continue

            opens     = candle_data['opens']
            closes    = candle_data['closes']
            volumes   = candle_data['volumes']
            vol_ratio = calc_vol_ratio(volumes)

            # 30min candles for earlier reversal confirmation
            candle_30m = fetch_candles(symbol, bar='30m', limit=50)
            if candle_30m:
                r_opens   = candle_30m['opens']
                r_closes  = candle_30m['closes']
                r_volumes = candle_30m['volumes']
            else:
                r_opens, r_closes, r_volumes = opens, closes, volumes

            sig   = generate_signal(calc_rsi(closes), calc_macd(closes), calc_bb(closes), vol_ratio)
            if TEST_FORCE_SIGNAL and symbol == 'BTC-USDT':
                sig = {'label': 'STRONG BUY', 'score': 5.0, 'reasons': ['TEST — forced signal, not real']}
            label = sig['label']
            zone  = direction_zone(label)

            prev         = cache.get(symbol, {})
            alerted_zone = prev.get('alerted_zone')
            alerted_at   = prev.get('alerted_at', 0)

            # Always persist latest label
            cache[symbol] = {**prev, 'label': label, 'zone': zone}

            print(f'  {symbol}: {label} (zone={zone}, last_alerted={alerted_zone or "—"})')

            # HOLD — never alert
            if zone == 'neutral':
                time.sleep(0.3)
                continue

            # Only alert on STRONG BUY — user relies on auto-exit orders for sells
            if label != 'STRONG BUY':
                time.sleep(0.3)
                continue

            # Reversal confirmation on 30min candles — earlier entry than 1H,
            # falls back to 1H data if 30min fetch failed.
            if not reversal_confirmed(r_opens, r_closes, r_volumes, zone):
                print(f'  {symbol}: {label} — no 30min reversal confirmation yet (candle/RSI still against signal)')
                time.sleep(0.3)
                continue

            # Same zone as last alert → suppress unless the reminder interval has passed.
            # This stops BUY↔STRONG BUY oscillation spam while still re-alerting every 4 h
            # so a coin that stays in BUY zone all day doesn't go completely silent.
            if zone == alerted_zone:
                secs_in_zone = now - alerted_at
                if secs_in_zone < REZONE_REMINDER:
                    print(f'  {symbol}: still in {zone} zone ({int(secs_in_zone/60)}m) — suppressed')
                    time.sleep(0.3)
                    continue
                print(f'  {symbol}: still in {zone} zone for {int(secs_in_zone/3600)}h — reminder alert')

            # Zone changed but flipped back too quickly — rapid oscillation guard
            if now - alerted_at < FLIP_COOLDOWN:
                secs_left = int(FLIP_COOLDOWN - (now - alerted_at))
                print(f'  {symbol}: zone flip within cooldown ({secs_left}s left) — suppressed')
                time.sleep(0.3)
                continue

            # Skip if there is already an active Option 3 trade on this coin
            if symbol in active_symbols:
                print(f'  {symbol}: STRONG BUY suppressed — active trade already running')
                time.sleep(0.3)
                continue

            # New zone entry — send alert
            send_telegram(format_alert(symbol, sig, ticker))
            cache[symbol] = {**cache[symbol], 'alerted_zone': zone, 'alerted_at': now}
            time.sleep(0.3)

        except Exception as e:
            print(f'  {symbol}: ERROR — {e}')


# ── OKX private API helpers ───────────────────────────────────────────────────
def _okx_sign(method, path, body=None):
    """Build OKX authentication headers (HMAC-SHA256)."""
    ts  = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    pre = ts + method + path + (json.dumps(body, separators=(',', ':')) if body else '')
    sig = base64.b64encode(
        hmac.new(OKX_SECRET_KEY.encode(), pre.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        'OK-ACCESS-KEY':        OKX_API_KEY,
        'OK-ACCESS-SIGN':       sig,
        'OK-ACCESS-TIMESTAMP':  ts,
        'OK-ACCESS-PASSPHRASE': OKX_PASSPHRASE,
        'Content-Type':         'application/json',
    }

def _okx_get(path):
    r = requests.get(OKX_BASE + path, headers=_okx_sign('GET', path), timeout=15)
    r.raise_for_status()
    d = r.json()
    if d.get('code') != '0':
        raise Exception(f"OKX {d.get('code')}: {d.get('msg', '')}")
    return d

def _okx_post(path, body):
    r = requests.post(OKX_BASE + path, headers=_okx_sign('POST', path, body), json=body, timeout=15)
    r.raise_for_status()
    d = r.json()
    if d.get('code') != '0':
        raise Exception(f"OKX {d.get('code')}: {d.get('msg', '')}")
    return d


# ── Option 3 trade monitor ────────────────────────────────────────────────────
def _fetch_option3_trades():
    """Fetch all active Option 3 trades (phase 1 and 2) from Supabase."""
    try:
        r = requests.get(
            # phase < 3 means active (1=waiting for TP/SL, 2=partial TP filled/trailing active)
            f'{SUPABASE_URL}/rest/v1/option3_trades?phase=lt.3&select=*',
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
        print(f'  [Option3] Supabase fetch error: {r.status_code}')
    except Exception as e:
        print(f'  [Option3] Supabase fetch failed: {e}')
    return []


def _is_algo_triggered(algo_id, ord_type='conditional'):
    """Return True if an OKX algo order has triggered (state=effective in history)."""
    try:
        d = _okx_get(f'/api/v5/trade/orders-algo-history?ordType={ord_type}&algoId={algo_id}')
        for order in d.get('data', []):
            if order.get('algoId') == algo_id and order.get('state') == 'effective':
                return True
    except Exception as e:
        print(f'  [Option3] Algo check error ({algo_id}): {e}')
    return False


def _get_fill_price(algo_id, ord_type='conditional'):
    """Get the actual fill price of a triggered OKX algo order."""
    try:
        d = _okx_get(f'/api/v5/trade/orders-algo-history?ordType={ord_type}&algoId={algo_id}')
        for order in d.get('data', []):
            if order.get('algoId') == algo_id and order.get('state') == 'effective':
                px = float(order.get('avgPx', '0') or '0')
                return px if px > 0 else None
    except Exception:
        return None
    return None


def _update_sl_to_breakeven(trade):
    """Cancel original SL, place a new conditional SL at the entry price."""
    symbol   = trade['symbol']
    entry_px = float(trade['entry_price'])
    sl_id    = trade['sl_id']
    sz_half  = trade['sz_half']

    # Cancel original SL (may already be gone — proceed anyway)
    try:
        _okx_post('/api/v5/trade/cancel-algos', [{'algoId': sl_id, 'instId': symbol}])
        print(f'  [Option3] {symbol}: original SL ({sl_id}) canceled ✓')
    except Exception as e:
        print(f'  [Option3] {symbol}: SL cancel warning: {e}')

    # Place break-even SL at entry price
    try:
        resp      = _okx_post('/api/v5/trade/order-algo', {
            'instId':          symbol,
            'tdMode':          'cash',
            'side':            'sell',
            'ordType':         'conditional',
            'sz':              sz_half,
            'slTriggerPx':     str(entry_px),
            'slOrdPx':         '-1',
            'slTriggerPxType': 'last',
        })
        new_sl_id = resp.get('data', [{}])[0].get('algoId', '')
        print(f'  [Option3] {symbol}: break-even SL placed at {entry_px} (ID: {new_sl_id}) ✓')
        return new_sl_id
    except Exception as e:
        print(f'  [Option3] {symbol}: break-even SL placement failed: {e}')
        return None


def _mark_phase2(trade_id, new_sl_id):
    """Update Supabase: set phase=2 and store the new break-even SL order ID."""
    try:
        r = requests.patch(
            f'{SUPABASE_URL}/rest/v1/option3_trades?id=eq.{trade_id}',
            headers={
                'apikey':        SUPABASE_KEY,
                'Authorization': f'Bearer {SUPABASE_KEY}',
                'Content-Type':  'application/json',
            },
            json={'phase': 2, 'sl_id': new_sl_id or ''},
            timeout=10,
        )
        if r.status_code in (200, 204):
            print(f'  [Option3] Trade {trade_id}: marked phase 2 ✓')
        else:
            print(f'  [Option3] Supabase phase update failed: {r.status_code}')
    except Exception as e:
        print(f'  [Option3] Supabase phase update error: {e}')


def _cancel_algo(symbol, algo_id):
    """Cancel an OKX algo order silently — already gone is fine."""
    try:
        _okx_post('/api/v5/trade/cancel-algos', [{'algoId': algo_id, 'instId': symbol}])
        print(f'  [Option3] {symbol}: cancelled algo {algo_id} ✓')
    except Exception as e:
        print(f'  [Option3] {symbol}: cancel algo {algo_id} warning: {e}')


def _mark_trade_closed(trade_id):
    """Mark a trade as phase=3 (fully closed) in Supabase."""
    try:
        r = requests.patch(
            f'{SUPABASE_URL}/rest/v1/option3_trades?id=eq.{trade_id}',
            headers={
                'apikey':        SUPABASE_KEY,
                'Authorization': f'Bearer {SUPABASE_KEY}',
                'Content-Type':  'application/json',
            },
            json={'phase': 3},
            timeout=10,
        )
        if r.status_code in (200, 204):
            print(f'  [Option3] Trade {trade_id}: marked closed ✓')
        else:
            print(f'  [Option3] Close update failed: {r.status_code}')
    except Exception as e:
        print(f'  [Option3] Close update error: {e}')


def monitor_option3_trades():
    """
    Monitor all active Option 3 trades (phase 1 and 2).
    Phase 1: detect partial TP fill → move SL to break-even, advance to phase 2.
             detect SL hit before TP  → report loss, close trade.
    Phase 2: detect trailing stop exit → report profit, close trade.
             detect break-even SL hit → report zero-loss exit, close trade.
    """
    if not OKX_API_KEY or not OKX_SECRET_KEY or not OKX_PASSPHRASE:
        return

    trades = _fetch_option3_trades()
    if not trades:
        return

    print(f'\n  [Option3] Monitoring {len(trades)} active trade(s)...')

    for trade in trades:
        symbol   = trade['symbol']
        phase    = trade.get('phase', 1)
        entry_px = float(trade.get('entry_price', 0) or 0)
        amt_usdt = float(trade.get('amount_usdt', 0) or 0)
        sz_half  = float(trade.get('sz_half', '0') or 0)
        ptp_pct  = float(trade.get('partial_tp_pct', 0) or 0)
        sl_pct   = float(trade.get('sl_pct', 0) or 0)
        coin     = symbol.replace('-USDT', '')

        try:
            if phase == 1:
                tp_id  = trade['partial_tp_id']
                sl_id  = trade.get('sl_id')
                # OCO format: both IDs are identical (single order covers TP and SL).
                # Old format: separate IDs. Backwards-compatible check.
                is_oco = (tp_id and sl_id and tp_id == sl_id)

                if _is_algo_triggered(tp_id, 'conditional'):
                    fill_px = _get_fill_price(tp_id, 'conditional')

                    # Determine which side of the OCO fired using fill price.
                    # Old separate-order trades: tp_id check always means TP fired.
                    tp_fired = (not is_oco) or (fill_px is not None and fill_px > entry_px)

                    if tp_fired:
                        # ─── Partial TP filled ───────────────────────────────────
                        print(f'  [Option3] {symbol}: partial TP triggered — moving SL to break-even...')
                        if fill_px and entry_px > 0:
                            profit_usdt = (fill_px - entry_px) * sz_half
                            profit_str  = f'+${profit_usdt:.2f} USDT'
                        else:
                            profit_str  = f'+{ptp_pct}% on 50% of position'

                        new_sl_id = _update_sl_to_breakeven(trade)
                        _mark_phase2(trade['id'], new_sl_id)
                        send_telegram(
                            f"✅ <b>Partial TP Hit — {coin}</b>\n"
                            f"💰 Profit locked: {profit_str}\n"
                            f"🛡️ SL moved to entry (break-even) — 2nd half is now risk-free\n"
                            f"🔄 Trailing stop protecting remaining 50%\n"
                            f"⏰ {time.strftime('%H:%M UTC')}"
                        )

                    else:
                        # ─── SL side of OCO fired ────────────────────────────────
                        print(f'  [Option3] {symbol}: SL triggered (OCO order) — closing full position...')
                        trailing_id = trade.get('trailing_id')
                        if trailing_id:
                            _cancel_algo(symbol, trailing_id)

                        second_half_sold = False
                        try:
                            _okx_post('/api/v5/trade/order', {
                                'instId': symbol,
                                'tdMode': 'cash',
                                'side':   'sell',
                                'ordType':'market',
                                'sz':     str(sz_half),
                            })
                            second_half_sold = True
                            print(f'  [Option3] {symbol}: remaining 50% market-sold ✓')
                        except Exception as e:
                            print(f'  [Option3] {symbol}: could not sell remaining 50%: {e}')

                        if fill_px and entry_px > 0:
                            loss_usdt = (entry_px - fill_px) * sz_half * 2
                            loss_str  = f'−${loss_usdt:.2f} USDT'
                        else:
                            loss_str  = f'−{sl_pct}% on full position'

                        _mark_trade_closed(trade['id'])
                        extra = '' if second_half_sold else '\n⚠️ Could not auto-sell remaining 50% — check OKX'
                        send_telegram(
                            f"🔴 <b>Stop Loss Hit — {coin}</b>\n"
                            f"💸 Loss: {loss_str}\n"
                            f"📍 Entry: {fmt_price(entry_px)}\n"
                            f"✅ Full position closed (both halves){extra}\n"
                            f"⏰ {time.strftime('%H:%M UTC')}"
                        )

                # ─── Old format: separate SL order (backwards compat) ─────────
                elif not is_oco and sl_id and _is_algo_triggered(sl_id, 'conditional'):
                    fill_px = _get_fill_price(sl_id, 'conditional')

                    _cancel_algo(symbol, tp_id)
                    trailing_id = trade.get('trailing_id')
                    if trailing_id:
                        _cancel_algo(symbol, trailing_id)

                    second_half_sold = False
                    try:
                        _okx_post('/api/v5/trade/order', {
                            'instId': symbol,
                            'tdMode': 'cash',
                            'side':   'sell',
                            'ordType':'market',
                            'sz':     str(sz_half),
                        })
                        second_half_sold = True
                        print(f'  [Option3] {symbol}: remaining 50% market-sold ✓')
                    except Exception as e:
                        print(f'  [Option3] {symbol}: could not sell remaining 50%: {e}')

                    if fill_px and entry_px > 0:
                        loss_usdt = (entry_px - fill_px) * sz_half * 2
                        loss_str  = f'−${loss_usdt:.2f} USDT'
                    else:
                        loss_str  = f'−{sl_pct}% on full position'

                    _mark_trade_closed(trade['id'])
                    extra = '' if second_half_sold else '\n⚠️ Could not auto-sell remaining 50% — check OKX'
                    send_telegram(
                        f"🔴 <b>Stop Loss Hit — {coin}</b>\n"
                        f"💸 Loss: {loss_str}\n"
                        f"📍 Entry: {fmt_price(entry_px)}\n"
                        f"✅ Full position closed (both halves){extra}\n"
                        f"⏰ {time.strftime('%H:%M UTC')}"
                    )

                else:
                    print(f'  [Option3] {symbol}: phase 1 — waiting for TP or SL')

            elif phase == 2:
                trailing_id = trade.get('trailing_id')
                be_sl_id    = trade.get('sl_id')  # updated to break-even SL in _mark_phase2

                # ─── Check if trailing stop exited ───────────────────────────
                if trailing_id and _is_algo_triggered(trailing_id, 'move_order_stop'):
                    fill_px = _get_fill_price(trailing_id, 'move_order_stop')
                    if fill_px and entry_px > 0 and fill_px > entry_px:
                        profit_usdt = (fill_px - entry_px) * sz_half
                        gain_pct    = (fill_px / entry_px - 1) * 100
                        profit_str  = f'+${profit_usdt:.2f} USDT (+{gain_pct:.1f}%)'
                    elif fill_px and entry_px > 0:
                        profit_str  = 'near break-even'
                    else:
                        profit_str  = 'exited via trailing stop'

                    _mark_trade_closed(trade['id'])
                    send_telegram(
                        f"🏁 <b>Trade Closed — {coin}</b>\n"
                        f"🔄 Trailing stop exit: {profit_str}\n"
                        f"✅ Phase 1 profit already secured earlier\n"
                        f"⏰ {time.strftime('%H:%M UTC')}"
                    )

                # ─── Check if break-even SL hit (zero loss on 2nd half) ──────
                elif be_sl_id and _is_algo_triggered(be_sl_id, 'conditional'):
                    _mark_trade_closed(trade['id'])
                    send_telegram(
                        f"⚪ <b>Break-Even Exit — {coin}</b>\n"
                        f"🛡️ Break-even SL hit — 2nd half exited at entry price\n"
                        f"✅ Phase 1 profit is secured — net result is positive\n"
                        f"⏰ {time.strftime('%H:%M UTC')}"
                    )

                else:
                    print(f'  [Option3] {symbol}: phase 2 — waiting for trailing stop or break-even SL')

        except Exception as e:
            print(f'  [Option3] {symbol}: error — {e}')


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    cache    = load_cache()
    start    = time.time()
    scan_num = 0

    while True:
        scan_num += 1
        elapsed = time.time() - start
        print(f'\n=== Scan #{scan_num} | +{elapsed:.0f}s | {time.strftime("%H:%M:%S UTC")} ===')

        run_scan(cache)
        monitor_option3_trades()
        save_cache(cache)

        elapsed   = time.time() - start
        remaining = LOOP_DURATION - elapsed

        if remaining <= CHECK_INTERVAL:
            print(f'\nLoop complete — {scan_num} scan(s) in {elapsed:.0f}s.')
            break

        print(f'Next scan in {CHECK_INTERVAL}s...')
        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    main()
