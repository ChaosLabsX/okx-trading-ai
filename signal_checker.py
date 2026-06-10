"""
TradingAI — Background Signal Checker
Runs every 5 minutes on GitHub Actions.
Sends Telegram alerts for STRONG BUY / STRONG SELL signals.
"""

import json
import math
import os
import time

import requests

# ── Coins to monitor ──────────────────────────────────────────────────────────
SYMBOLS = [
    'BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'BNB-USDT', 'XRP-USDT',
    'DOGE-USDT', 'ADA-USDT', 'AVAX-USDT', 'MATIC-USDT', 'DOT-USDT',
]

OKX_BASE           = 'https://www.okx.com'
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID   = os.environ.get('TELEGRAM_CHAT_ID', '')
CACHE_FILE         = 'signal_cache.json'


# ── OKX data fetching ─────────────────────────────────────────────────────────
def fetch_candles(symbol, bar='1H', limit=100):
    url = f'{OKX_BASE}/api/v5/market/candles?instId={symbol}&bar={bar}&limit={limit}'
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data['code'] != '0' or not data.get('data'):
        return []
    # OKX returns newest-first — reverse to chronological order
    candles = list(reversed(data['data']))
    return [float(c[4]) for c in candles]  # close prices only


def fetch_ticker(symbol):
    url = f'{OKX_BASE}/api/v5/market/ticker?instId={symbol}'
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data['code'] != '0' or not data.get('data'):
        return None
    t = data['data'][0]
    last = float(t['last'])
    open24 = float(t['open24h'])
    change_pct = (last - open24) / open24 * 100 if open24 else 0
    return {'price': last, 'change_pct': change_pct}


# ── Technical indicators (matches app.js exactly) ────────────────────────────
def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    avg_gain = avg_loss = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d > 0:
            avg_gain += d
        else:
            avg_loss -= d
    avg_gain /= period
    avg_loss /= period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


def ema_array(vals, period):
    k = 2 / (period + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def calc_macd(closes):
    if len(closes) < 35:
        return None
    ema12 = ema_array(closes, 12)
    ema26 = ema_array(closes, 26)
    macd_line = [a - b for a, b in zip(ema12[25:], ema26[25:])]
    sig_line  = ema_array(macd_line, 9)
    n = len(macd_line) - 1
    bullish_cross = n > 0 and macd_line[n - 1] < sig_line[n - 1] and macd_line[n] >= sig_line[n]
    bearish_cross = n > 0 and macd_line[n - 1] > sig_line[n - 1] and macd_line[n] <= sig_line[n]
    return {
        'trend':         'bullish' if macd_line[n] > sig_line[n] else 'bearish',
        'bullish_cross': bullish_cross,
        'bearish_cross': bearish_cross,
    }


def calc_bb(closes, period=20):
    if len(closes) < period:
        return None
    sl   = closes[-period:]
    mean = sum(sl) / period
    std  = math.sqrt(sum((x - mean) ** 2 for x in sl) / period)
    upper = mean + 2 * std
    lower = mean - 2 * std
    pct_b = (closes[-1] - lower) / (upper - lower) if upper > lower else 0.5
    return {'pct_b': pct_b}


# ── Signal engine (matches app.js exactly) ────────────────────────────────────
def generate_signal(rsi, macd, bb):
    score   = 0.0
    reasons = []

    if rsi is not None:
        if rsi <= 20:
            score += 3; reasons.append(f'RSI {rsi:.0f} — extremely oversold')
        elif rsi <= 30:
            score += 2; reasons.append(f'RSI {rsi:.0f} — oversold')
        elif rsi <= 40:
            score += 1; reasons.append(f'RSI {rsi:.0f} — below neutral')
        elif rsi >= 80:
            score -= 3; reasons.append(f'RSI {rsi:.0f} — extremely overbought')
        elif rsi >= 70:
            score -= 2; reasons.append(f'RSI {rsi:.0f} — overbought')
        elif rsi >= 60:
            score -= 1; reasons.append(f'RSI {rsi:.0f} — above neutral')

    if macd is not None:
        if macd['bullish_cross']:
            score += 2; reasons.append('MACD bullish crossover')
        elif macd['bearish_cross']:
            score -= 2; reasons.append('MACD bearish crossover')
        elif macd['trend'] == 'bullish':
            score += 0.5
        else:
            score -= 0.5

    if bb is not None:
        if bb['pct_b'] <= 0.05:
            score += 2; reasons.append('Price at lower Bollinger Band')
        elif bb['pct_b'] <= 0.20:
            score += 1; reasons.append('Price near lower BB')
        elif bb['pct_b'] >= 0.95:
            score -= 2; reasons.append('Price at upper Bollinger Band')
        elif bb['pct_b'] >= 0.80:
            score -= 1; reasons.append('Price near upper BB')

    if score >= 4:
        label = 'STRONG BUY'
    elif score >= 2:
        label = 'BUY'
    elif score > -2:
        label = 'HOLD'
    elif score > -4:
        label = 'SELL'
    else:
        label = 'STRONG SELL'

    return {'label': label, 'score': score, 'reasons': reasons}


# ── Helpers ───────────────────────────────────────────────────────────────────
def fmt_price(price):
    if price >= 10000:
        return f'${price:,.2f}'
    if price >= 1:
        return f'${price:.4f}'
    return f'${price:.6f}'


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print('Telegram credentials not set — skipping.')
        return
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    r = requests.post(url, json={
        'chat_id':    TELEGRAM_CHAT_ID,
        'text':       message,
        'parse_mode': 'HTML',
    }, timeout=15)
    if r.status_code == 200:
        print(f'Telegram sent OK')
    else:
        print(f'Telegram error {r.status_code}: {r.text}')


def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_cache(state):
    with open(CACHE_FILE, 'w') as f:
        json.dump(state, f, indent=2)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    prev_state = load_cache()
    new_state  = {}
    alerts     = []

    print(f'Checking {len(SYMBOLS)} coins...')

    for symbol in SYMBOLS:
        try:
            closes = fetch_candles(symbol)
            ticker = fetch_ticker(symbol)
            if not closes or not ticker:
                print(f'  {symbol}: no data')
                continue

            rsi  = calc_rsi(closes)
            macd = calc_macd(closes)
            bb   = calc_bb(closes)
            sig  = generate_signal(rsi, macd, bb)

            new_state[symbol] = sig['label']
            print(f'  {symbol}: {sig["label"]} (score {sig["score"]:.1f})')

            is_strong = sig['label'] in ('STRONG BUY', 'STRONG SELL')
            was_same  = prev_state.get(symbol) == sig['label']

            if is_strong and not was_same:
                coin    = symbol.replace('-USDT', '')
                emoji   = '🟢' if sig['label'] == 'STRONG BUY' else '🔴'
                reasons = '\n📊 '.join(sig['reasons']) if sig['reasons'] else 'Multiple indicators aligned'
                alerts.append(
                    f"{emoji} <b>{sig['label']}: {coin}</b>\n"
                    f"💰 Price: {fmt_price(ticker['price'])} ({ticker['change_pct']:+.2f}% 24h)\n"
                    f"📊 {reasons}"
                )

            time.sleep(0.4)  # stay within OKX rate limits

        except Exception as e:
            print(f'  {symbol}: ERROR — {e}')

    if alerts:
        header = f"🚨 <b>TradingAI Alert</b>\n\n"
        send_telegram(header + '\n\n─────────────────\n\n'.join(alerts))
        print(f'Sent {len(alerts)} alert(s) to Telegram.')
    else:
        print('No new strong signals — nothing sent.')

    save_cache(new_state)
    print('Done.')


if __name__ == '__main__':
    main()
