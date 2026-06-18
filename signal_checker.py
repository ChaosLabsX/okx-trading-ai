"""
TradingAI — Background Signal Checker (24/7 mode)
Runs every 5 minutes on GitHub Actions.
Each run loops internally for ~4 minutes (one scan every 60 s).

Alert rules:
- BUY and STRONG BUY are the same zone — oscillating between them produces NO extra alert.
- SELL and STRONG SELL are the same zone — same rule.
- A new alert fires only when the zone CHANGES (entering BUY zone, flipping to SELL, etc.).
- 2-minute safety cooldown prevents false alerts from rapid back-and-forth oscillation.
- SELL/STRONG SELL alerts are filtered to coins you actually hold (read live from Supabase).
"""

import json
import math
import os
import time

import requests

# ── Config ────────────────────────────────────────────────────────────────────
SYMBOLS = [
    'BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'BNB-USDT', 'XRP-USDT',
    'DOGE-USDT', 'ADA-USDT', 'AVAX-USDT', 'MATIC-USDT', 'DOT-USDT',
]

OKX_BASE           = 'https://www.okx.com'
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID', '')
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


# ── Portfolio from Supabase ───────────────────────────────────────────────────
def fetch_portfolio_symbols():
    """
    Returns the set of OKX symbols in your portfolio (e.g. {'BTC-USDT', 'ETH-USDT'}).
    The browser app pushes this to Supabase automatically on every portfolio change.
    Returns empty set if Supabase is unreachable — SELL alerts are skipped in that case.
    """
    try:
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/app_settings?id=eq.main&select=portfolio_symbols',
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            raw  = (data[0].get('portfolio_symbols') or '') if data else ''
            syms = {s.strip() for s in raw.split(',') if s.strip()}
            if syms:
                print(f'  Portfolio: {", ".join(sorted(syms))}')
            else:
                print('  Portfolio: empty — SELL alerts will be skipped')
            return syms
    except Exception as e:
        print(f'  Portfolio fetch failed: {e} — SELL alerts skipped')
    return set()


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
def run_scan(cache, portfolio_symbols):
    now = time.time()
    for symbol in SYMBOLS:
        try:
            candle_data = fetch_candles(symbol)
            ticker      = fetch_ticker(symbol)
            if not candle_data or not ticker:
                print(f'  {symbol}: no data')
                continue

            closes    = candle_data['closes']
            volumes   = candle_data['volumes']
            vol_ratio = calc_vol_ratio(volumes)

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

            # SELL filter — skip if coin not in portfolio
            if label in SELL_LABELS and symbol not in portfolio_symbols:
                print(f'  {symbol}: SELL skipped — not in portfolio')
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

            # New zone entry — send alert
            send_telegram(format_alert(symbol, sig, ticker))
            cache[symbol] = {**cache[symbol], 'alerted_zone': zone, 'alerted_at': now}
            time.sleep(0.3)

        except Exception as e:
            print(f'  {symbol}: ERROR — {e}')


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    cache    = load_cache()
    start    = time.time()
    scan_num = 0

    while True:
        scan_num += 1
        elapsed = time.time() - start
        print(f'\n=== Scan #{scan_num} | +{elapsed:.0f}s | {time.strftime("%H:%M:%S UTC")} ===')

        portfolio = fetch_portfolio_symbols()
        run_scan(cache, portfolio)
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
