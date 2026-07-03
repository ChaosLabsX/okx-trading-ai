"""
TradingAI — Background Signal Checker (24/7 mode)
Runs every 5 minutes on GitHub Actions.
Each run loops internally for ~4 minutes (one scan every 60 s).

Alert rules:
- BUY and STRONG BUY are the same zone — oscillating between them produces NO extra alert.
- SELL and STRONG SELL are the same zone — same rule.
- A new alert fires only when the zone CHANGES (entering BUY zone, flipping to SELL, etc.).
- 2-minute safety cooldown prevents false alerts from rapid back-and-forth oscillation.

Auto-trade (Claude Haiku):
- When STRONG BUY + reversal confirmed, Claude Haiku decides whether to trade and sets
  parameters (USDT amount, TP%, SL%, trailing%) based on coin volatility + signal strength.
- Claude also sees recent live trade results (from Supabase) so it can skip/downsize
  setups that have been losing.
- If approved: places market buy + OCO (TP/SL on 50%) + conditional SL (other 50%) on
  OKX, so the FULL position is stop-loss-protected server-side 24/7. When the TP fires,
  the monitor swaps the 2nd-half SL for an immediately-active trailing stop.
- Safety rails (production only): BTC regime filter (no dip-buying while BTC is in a
  4H downtrend), max concurrent trades cap, and a daily stop-loss circuit breaker.
- Telegram notification includes "Trade Already Placed on OKX ✅" with full parameters.
- Requires CLAUDE_API_KEY GitHub secret. Auto-trade silently skips if key is missing.
"""

import base64
import hashlib
import hmac
import json
import math
import os
import re
import time
from datetime import datetime, timezone, timedelta

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
CLAUDE_API_KEY     = os.environ.get('CLAUDE_API_KEY', '')
CLAUDE_MODEL       = 'claude-haiku-4-5-20251001'
CACHE_FILE         = 'signal_cache.json'

SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')

LOOP_DURATION  = 4 * 60   # seconds per GitHub Actions run
CHECK_INTERVAL = 60        # seconds between scans

EMOJI = {'STRONG BUY': '🟢', 'BUY': '🔵', 'SELL': '🟠', 'STRONG SELL': '🔴'}

# Minimum seconds between any two alerts for the same coin.
# A genuine zone flip within this window is suppressed — rapid oscillation is noise.
FLIP_COOLDOWN  = 2 * 60
OKX_FEE_RATE   = 0.001   # 0.1% taker fee (adjust if your VIP tier is different)

# If a coin stays in the same BUY/SELL zone longer than this, send a reminder alert.
REZONE_REMINDER = 4 * 60 * 60  # 4 hours

# Set to True to force a STRONG BUY alert for BTC on the next run — bypasses all
# market logic so you can confirm GitHub Actions → Telegram → auto-trade end-to-end.
# Delete the cache on GitHub (Actions → Caches) before running, then set back to False.
TEST_FORCE_SIGNAL = False

# ── TEST MODE ─────────────────────────────────────────────────────────────────
# Makes Option 3 trades trigger EASILY with a tiny fixed size — purely to test the
# trade → monitor → Telegram pipeline end to end. When True it:
#   • lowers the STRONG BUY bar from score ≥ 5 to score ≥ 1
#   • skips the 30-min reversal confirmation gate
#   • skips the Claude AI advisor and uses a fixed size + fixed TP/SL/trail
#   • only ever keeps ONE test trade alive at a time (waits for it to close)
#
# ►►►  TO RESTORE NORMAL (PRODUCTION) BEHAVIOUR: set TEST_MODE = False  ◄◄◄
#      That one line reverts everything — all production values are preserved below.
TEST_MODE = True

# Test bar of 1.0 fires on very common conditions (e.g. bullish MACD trend +0.5
# plus price near the lower Bollinger Band +1) so test trades start quickly.
STRONG_BUY_SCORE  =  1.0 if TEST_MODE else  5.0   # score needed to label STRONG BUY
STRONG_SELL_SCORE = -1.0 if TEST_MODE else -5.0   # score needed to label STRONG SELL
MIN_TRADE_USDT    = 10.0                           # balance gate: no trading below this USDT balance (test & prod)
# Tight, cheap test parameters: trades resolve within minutes-to-hours instead of
# hours-to-days, and a worst-case (SL) test costs ≈ $0.10 + ~$0.01 fees.
# Each half-position sell is ≈ $2.50 — still above OKX minimum order sizes for
# every coin in SYMBOLS (largest minimum is BTC: 0.00001 BTC per order).
TEST_TRADE_USDT   =  5.0                           # fixed trade size used in TEST_MODE
TEST_TP_PCT       =  1.5                           # fixed take-profit % in TEST_MODE
TEST_SL_PCT       =  2.0                           # fixed stop-loss %  in TEST_MODE
TEST_TRAIL_PCT    =  1.0                           # fixed trailing %   in TEST_MODE

if TEST_MODE:
    # Shorten the anti-spam suppression so test trades re-trigger promptly
    # (production keeps the 4h reminder / 2min flip cooldown defined above).
    REZONE_REMINDER = 10 * 60   # 10 minutes instead of 4 hours
    FLIP_COOLDOWN   = 30        # 30 seconds instead of 2 minutes


# ── Zone helpers ──────────────────────────────────────────────────────────────
def direction_zone(label):
    if label in ('STRONG BUY', 'BUY'):   return 'up'
    if label in ('STRONG SELL', 'SELL'): return 'down'
    return 'neutral'


# ── OKX public data ───────────────────────────────────────────────────────────
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
    if len(volumes) < 21:
        return None
    avg = sum(volumes[-21:-1]) / 20
    return volumes[-1] / avg if avg > 0 else None


def reversal_confirmed(opens, closes, volumes, zone):
    """
    Guards against alerting into a falling knife (BUY) or rising knife (SELL).
    Uses 30min candle data for earlier entry detection, falls back to 1H.
    Requires ALL three on the most recent candle:
      BUY  → green candle AND RSI turning up AND volume ≥ 1× avg
      SELL → red candle  AND RSI turning down AND volume ≥ 1× avg
    """
    if len(closes) < 16 or len(opens) < 2:
        return True
    rsi_now  = calc_rsi(closes)
    rsi_prev = calc_rsi(closes[:-1])
    if rsi_now is None or rsi_prev is None:
        return True
    vol_ok = True
    if volumes and len(volumes) >= 21:
        avg_vol = sum(volumes[-21:-1]) / 20
        vol_ok  = avg_vol > 0 and volumes[-1] >= avg_vol * 1.0
    if zone == 'up':
        return closes[-1] >= opens[-1] and rsi_now >= rsi_prev and vol_ok
    if zone == 'down':
        return closes[-1] <= opens[-1] and rsi_now <= rsi_prev and vol_ok
    return True


def generate_signal(rsi, macd, bb, vol_ratio=None, rsi_4h=None):
    score, reasons = 0.0, []

    if rsi is not None:
        if   rsi <= 20: score += 3; reasons.append(f'RSI {rsi:.0f} — extremely oversold')
        elif rsi <= 30: score += 2; reasons.append(f'RSI {rsi:.0f} — oversold')
        elif rsi <= 40:             reasons.append(f'RSI {rsi:.0f} — below neutral')   # no score bonus
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

    # 4H RSI confirmation — mirrors the browser dashboard logic (max ±1 point)
    if rsi_4h is not None:
        if   score > 0 and rsi_4h <= 40:
            score += 1;   reasons.append(f'4H RSI {rsi_4h:.0f} — higher-TF uptrend confirmed')
        elif score < 0 and rsi_4h >= 55:
            score -= 1;   reasons.append(f'4H RSI {rsi_4h:.0f} — higher-TF downtrend confirmed')
        elif score > 0 and rsi_4h >= 70:
            score -= 0.5; reasons.append(f'4H RSI {rsi_4h:.0f} — caution: overbought on 4H')
        elif score < 0 and rsi_4h <= 30:
            score += 0.5; reasons.append(f'4H RSI {rsi_4h:.0f} — caution: oversold on 4H')

    label = ('STRONG BUY'  if score >= STRONG_BUY_SCORE  else
             'BUY'         if score >= 2  else
             'STRONG SELL' if score <= STRONG_SELL_SCORE else
             'SELL'        if score <= -2 else 'HOLD')
    return {'label': label, 'score': score, 'reasons': reasons}


def btc_regime_ok():
    """
    BTC regime filter: the signal engine buys oversold dips, which bleeds money when
    the whole market is trending down (alts are ~80% correlated with BTC). Block new
    buys only when BTC's higher timeframe is clearly bearish:
        price below the 4H EMA-50  AND  4H RSI < 45.
    Fails OPEN (allows trading) if BTC data can't be fetched — logged loudly.
    Returns (ok, reason_string).
    """
    try:
        c = fetch_candles('BTC-USDT', bar='4H', limit=100)
        if not c or len(c['closes']) < 60:
            return True, 'BTC 4H data unavailable — regime check skipped'
        closes = c['closes']
        price  = closes[-1]
        ema50  = ema_array(closes, 50)[-1]
        rsi4h  = calc_rsi(closes)
        if price < ema50 and rsi4h is not None and rsi4h < 45:
            return False, (f'BTC ${price:,.0f} < 4H EMA50 ${ema50:,.0f} and '
                           f'4H RSI {rsi4h:.0f} — bearish regime, dip-buys blocked')
        rsi_s = f'{rsi4h:.0f}' if rsi4h is not None else 'N/A'
        return True, f'BTC ${price:,.0f} vs 4H EMA50 ${ema50:,.0f}, RSI {rsi_s} — OK to trade'
    except Exception as e:
        return True, f'regime check error ({e}) — allowing trades'


# ── Helpers ───────────────────────────────────────────────────────────────────
def fmt_price(p):
    return f'${p:,.2f}' if p >= 10000 else f'${p:.4f}' if p >= 1 else f'${p:.6f}'


def fmt_usdt(v):
    """Signed USDT amount, e.g. +$1.23 / −$0.0450 (4 decimals for tiny values)."""
    sign = '+' if v >= 0 else '−'
    a = abs(v)
    return f'{sign}${a:.2f}' if a >= 0.10 else f'{sign}${a:.4f}'


def _exit_pnl(entry_px, fill_px, sz):
    """
    Calculate net P&L and fee breakdown for a single exit (one half-position).
    Returns (net_pnl, total_fees, buy_fee, sell_fee) all in USDT.
    buy_fee  = proportional share of the original entry fee for this quantity.
    sell_fee = OKX fee charged on this exit.
    net_pnl  = gross price gain minus both fees.
    """
    gross_pnl  = (fill_px - entry_px) * sz
    buy_fee    = entry_px * sz * OKX_FEE_RATE
    sell_fee   = fill_px  * sz * OKX_FEE_RATE
    total_fees = buy_fee + sell_fee
    net_pnl    = gross_pnl - total_fees
    return net_pnl, total_fees, buy_fee, sell_fee


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


def format_alert(symbol, sig, ticker, trade_result=None):
    """
    Build the Telegram message for a signal alert.
    trade_result: None = auto-trade not configured
                  dict  = trade placed successfully (contains amount_usdt, tp_pct, etc.)
                  'skip'  = Claude decided not to trade
                  'error' = trade placement failed
    """
    coin    = symbol.replace('-USDT', '')
    emoji   = EMOJI.get(sig['label'], '⚪')
    reasons = ' · '.join(sig['reasons']) if sig['reasons'] else 'Multiple indicators aligned'
    score   = sig['score']
    msg = (
        f"{emoji} <b>{sig['label']}: {coin}</b>  [{score:+.1f}]\n"
        f"💰 {fmt_price(ticker['price'])} ({ticker['change_pct']:+.2f}% 24h)\n"
        f"📊 {reasons}"
    )
    if isinstance(trade_result, dict):
        msg += (
            f"\n\n✅ <b>Trade Already Placed on OKX</b>\n"
            f"💵 ${trade_result['amount_usdt']:.2f} USDT\n"
            f"🎯 TP +{trade_result['tp_pct']}%  ·  "
            f"🛡️ SL −{trade_result['sl_pct']}%  ·  "
            f"🔄 Trail {trade_result['trail_pct']}%"
        )
        if not trade_result.get('saved', True):
            msg += (
                f"\n⚠️ <b>NOT saved to tracking DB</b> — break-even SL move & exit "
                f"alerts are OFF for this trade. Fix Supabase secrets (see Actions log)."
            )
    elif trade_result == 'skip':
        msg += '\n\n⏭️ <i>AI skipped — setup not optimal right now</i>'
    elif trade_result == 'error':
        msg += '\n\n⚠️ <i>Auto-trade failed — place manually on OKX if desired</i>'
    elif trade_result == 'cap':
        msg += '\n\n⏩ <i>Signal qualified but ranked below top 2 this scan — no trade placed</i>'
    return msg


# ── Cache ─────────────────────────────────────────────────────────────────────
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


# ── OKX private API ───────────────────────────────────────────────────────────
def _okx_sign(method, path, body=None):
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
    # Must send the exact same compact JSON that was used to compute the HMAC signature.
    # requests' json= kwarg adds spaces which would produce a different byte sequence → 401.
    body_str = json.dumps(body, separators=(',', ':'))
    r = requests.post(
        OKX_BASE + path,
        headers=_okx_sign('POST', path, body),
        data=body_str,
        timeout=15,
    )
    r.raise_for_status()
    d = r.json()
    if d.get('code') != '0':
        raise Exception(f"OKX {d.get('code')}: {d.get('msg', '')}")
    return d


def _fetch_usdt_balance():
    """Return available USDT balance from OKX spot account."""
    try:
        d = _okx_get('/api/v5/account/balance?ccy=USDT')
        for item in d.get('data', [{}])[0].get('details', []):
            if item.get('ccy') == 'USDT':
                return float(item.get('availBal', 0) or 0)
    except Exception as e:
        print(f'  [OKX] Balance fetch error: {e}')
    return 0.0


# ── Claude Haiku — AI trade advisor ──────────────────────────────────────────
def ai_trade_params(symbol, sig, ticker, usdt_balance, rsi_1h, rsi_4h, macd_data, bb_data, vol_ratio):
    """
    Ask Claude Haiku whether this STRONG BUY is worth trading and what parameters to use.
    Returns a dict with trade params, or None if Claude says SKIP.
    """
    coin     = symbol.replace('-USDT', '')
    rsi_1h_s = f'{rsi_1h:.1f}' if rsi_1h is not None else 'N/A'
    rsi_4h_s = f'{rsi_4h:.1f}' if rsi_4h is not None else 'N/A'

    if macd_data:
        if macd_data['bullish_cross']:        macd_s = 'Bullish crossover ✓'
        elif macd_data['bearish_cross']:      macd_s = 'Bearish crossover'
        elif macd_data['trend'] == 'bullish': macd_s = 'Bullish trend'
        else:                                 macd_s = 'Bearish trend'
    else:
        macd_s = 'N/A'

    if bb_data:
        zone_s = 'oversold zone' if bb_data['pct_b'] < 0.2 else 'overbought zone' if bb_data['pct_b'] > 0.8 else 'mid-range'
        bb_s   = f'{bb_data["pct_b"] * 100:.0f}% B — {zone_s}'
    else:
        bb_s = 'N/A'

    vol_s = f'{vol_ratio:.1f}× average' if vol_ratio is not None else 'N/A'

    history   = _trade_history_context(symbol)
    history_s = f'\n\nRECENT LIVE RESULTS (this bot\'s actual closed trades):\n{history}' if history else ''

    system = f"""You are an expert crypto trading advisor for OKX spot markets (no leverage, no futures).
A STRONG BUY signal has been confirmed with reversal on 30-minute candle. Decide if this trade is worth placing and output the optimal Option 3 parameters.

CAPITAL & POSITION SIZING:
Available USDT: ${usdt_balance:.2f}
- Score 4.0–4.4, 2 confirmations → 10–15% of capital
- Score 4.5–4.9, 2–3 confirmations → 15–20% of capital
- Score 5.0+, 3+ confirmations → 20–30% of capital
Hard cap: never exceed 30% per trade. Minimum $10 USDT.

OPTION 3 PARAMETERS by volatility tier:
Extreme (PEPE, WIF, DOGE, BONK, FLOKI): partialTpPct 7–10, trailingCallbackPct 4–5, slPct 9–12
High (AVAX, SOL, SUI, INJ, TIA, APT, ENA, RUNE, JUP, SEI, OP, ARB, NEAR, FET, STRK): partialTpPct 5–8, trailingCallbackPct 3–4, slPct 7–10
Medium (BTC, ETH, XRP, ADA, LINK, DOT, ATOM, HBAR, TRX, TON, ONDO, LDO, POL): partialTpPct 3–5, trailingCallbackPct 2–3, slPct 5–7

INCREASE partialTpPct for stronger signals (let winners run):
- Score ≥ 5.0: +2%
- Score 4.5–4.9: +1%
- RSI 1H ≤ 25 (deeply oversold): +1%
- RSI 4H ≤ 35 (both timeframes oversold): +1%
- MACD bullish crossover confirmed: +0.5%

DO NOT TRADE ([SKIP]) if:
- RSI 4H > 65 (higher timeframe overbought — bad risk/reward entry)
- Fewer than 2 indicator confirmations in the reasons list
- Available USDT < $10

RECENT LIVE RESULTS (when provided) are this bot's actual trade outcomes — weigh them:
- This coin's last trades repeatedly hit stop-loss → require a stronger setup or SKIP
- Overall results negative → size toward the LOWER end of the capital range
- Overall results positive and this coin has been winning → normal sizing rules apply

Respond with EXACTLY ONE line — nothing else:
Trade:  [TRADE:{{"side":"buy","symbol":"{symbol}","amountUsdt":0,"partialTpPct":0,"trailingCallbackPct":0,"slPct":0}}]
Skip:   [SKIP: one-line reason]"""

    user_msg = f"""STRONG BUY confirmed — {coin}

Price: {fmt_price(ticker['price'])} ({ticker['change_pct']:+.2f}% 24h)
Signal score: {sig['score']:+.1f}
Confirmed by: {', '.join(sig['reasons']) if sig['reasons'] else 'multiple indicators'}

RSI 1H:  {rsi_1h_s}
RSI 4H:  {rsi_4h_s}
MACD:    {macd_s}
BB:      {bb_s}
Volume:  {vol_s}
USDT available: ${usdt_balance:.2f}{history_s}

Place this trade?"""

    try:
        r = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key':         CLAUDE_API_KEY,
                'anthropic-version': '2023-06-01',
                'content-type':      'application/json',
            },
            json={
                'model':      CLAUDE_MODEL,
                'max_tokens': 200,
                'system':     system,
                'messages':   [{'role': 'user', 'content': user_msg}],
            },
            timeout=30,
        )
        r.raise_for_status()
        text = r.json()['content'][0]['text'].strip()
        print(f'  [Claude] {coin}: {text[:150]}')

        # Parse TRADE tag
        m = re.search(r'\[TRADE:(\{.*?\})\]', text, re.DOTALL)
        if m:
            p      = json.loads(m.group(1))
            amount = float(p.get('amountUsdt', 0))
            if amount < 10:
                print(f'  [Claude] Amount too small (${amount:.2f}) — skipping')
                return None
            # Safety cap: never let Claude exceed 30% of balance
            cap = usdt_balance * 0.30
            if amount > cap:
                print(f'  [Claude] Amount ${amount:.2f} exceeds 30% cap — capped at ${cap:.2f}')
                amount = cap
            return {
                'amount_usdt':    round(amount, 2),
                'partial_tp_pct': float(p.get('partialTpPct', 5)),
                'trailing_pct':   float(p.get('trailingCallbackPct', 3)),
                'sl_pct':         float(p.get('slPct', 7)),
            }

        # Parse SKIP tag
        if re.search(r'\[SKIP', text, re.IGNORECASE):
            m2 = re.search(r'\[SKIP[:\s]*(.*?)\]', text, re.IGNORECASE)
            reason = m2.group(1).strip() if m2 else 'no reason given'
            print(f'  [Claude] SKIP — {reason}')
            return None

        print(f'  [Claude] Unexpected response format — skipping trade')
        return None

    except Exception as e:
        print(f'  [Claude] API error: {e}')
        return None


# ── Option 3 auto-trade placement ────────────────────────────────────────────
def _save_option3_trade(trade_data):
    """Persist Option 3 trade to Supabase so the monitor can track it. Returns True on success."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print('  [Supabase] URL or key not set — trade NOT saved (monitor cannot track it). '
              'Check SUPABASE_URL / SUPABASE_KEY GitHub Secrets.')
        return False
    try:
        r = requests.post(
            f'{SUPABASE_URL}/rest/v1/option3_trades',
            headers={
                'apikey':        SUPABASE_KEY,
                'Authorization': f'Bearer {SUPABASE_KEY}',
                'Content-Type':  'application/json',
                'Prefer':        'resolution=merge-duplicates,return=minimal',
            },
            json=trade_data,
            timeout=10,
        )
        if r.status_code in (200, 201, 204):
            print(f'  [Supabase] Trade saved ✓ (id={trade_data.get("id")})')
            return True
        # If the sl2_id column doesn't exist yet (migration not run), retry without it
        # so the trade is at least tracked in legacy mode.
        if 'sl2_id' in trade_data:
            slim = {k: v for k, v in trade_data.items() if k != 'sl2_id'}
            r2 = requests.post(
                f'{SUPABASE_URL}/rest/v1/option3_trades',
                headers={
                    'apikey':        SUPABASE_KEY,
                    'Authorization': f'Bearer {SUPABASE_KEY}',
                    'Content-Type':  'application/json',
                    'Prefer':        'resolution=merge-duplicates,return=minimal',
                },
                json=slim,
                timeout=10,
            )
            if r2.status_code in (200, 201, 204):
                print(f'  [Supabase] Trade saved WITHOUT sl2_id — run the SQL migration '
                      f'in docs/ARCHITECTURE.md to enable full 2nd-half tracking!')
                return True
        # Loud failure: exact status + body + which keys we sent, so the log pinpoints the cause
        print(f'  [Supabase] Save FAILED: HTTP {r.status_code} — {r.text[:300]}')
        print(f'  [Supabase] Payload keys sent: {list(trade_data.keys())}')
        return False
    except Exception as e:
        print(f'  [Supabase] Save error: {e}')
        return False


def place_option3_trade(symbol, params, ticker):
    """
    Execute a full Option 3 trade on OKX:
      1. Market buy (full amount)
      2. OCO conditional — TP + SL on first 50% (single order avoids balance reservation issue)
      3. Conditional SL on remaining 50% at the same trigger price — the FULL position
         is stop-loss-protected server-side 24/7. The monitor swaps this for an
         immediately-active trailing stop once the partial TP fills.
      4. Save to Supabase for monitoring

    Raises on failure so the caller can send the appropriate Telegram message.
    Returns a dict with trade summary on success.
    """
    price     = ticker['price']
    amt_usdt  = params['amount_usdt']
    tp_pct    = params['partial_tp_pct']
    sl_pct    = params['sl_pct']
    trail_pct = params['trailing_pct']

    # 1. Market buy
    _okx_post('/api/v5/trade/order', {
        'instId':  symbol,
        'tdMode':  'cash',
        'side':    'buy',
        'ordType': 'market',
        'sz':      f'{amt_usdt:.4f}',
        'tgtCcy':  'quote_ccy',
    })
    print(f'  [Trade] {symbol}: market buy ${amt_usdt:.2f} USDT ✓')

    # Give OKX 1.5 s to register the fill before placing algo orders
    time.sleep(1.5)

    # Estimated coin quantity — approximation used for algo order sizing
    sz_coin  = amt_usdt / price
    half_sz  = sz_coin * 0.5 * 0.9985  # 50% with OKX fee haircut

    tp_price  = price * (1 + tp_pct  / 100)
    sl_price  = price * (1 - sl_pct  / 100)
    base_algo = {'instId': symbol, 'tdMode': 'cash', 'side': 'sell'}

    # 2. OCO: TP and SL on first 50% (one order — OKX only reserves balance once)
    oco_resp = _okx_post('/api/v5/trade/order-algo', {
        **base_algo,
        'ordType':          'conditional',
        'sz':               f'{half_sz:.8f}',
        'tpTriggerPx':      f'{tp_price:.8f}',
        'tpOrdPx':          '-1',
        'tpTriggerPxType':  'last',
        'slTriggerPx':      f'{sl_price:.8f}',
        'slOrdPx':          '-1',
        'slTriggerPxType':  'last',
    })
    oco_id = oco_resp.get('data', [{}])[0].get('algoId', '')
    print(f'  [Trade] {symbol}: OCO TP +{tp_pct}% / SL −{sl_pct}% (ID: {oco_id}) ✓')

    # 3. Conditional SL on remaining 50% at the same trigger price — full position
    #    protected on OKX even if the monitor is down. Swapped for a trailing stop
    #    by the monitor when the TP fires.
    sl2_resp = _okx_post('/api/v5/trade/order-algo', {
        **base_algo,
        'ordType':         'conditional',
        'sz':              f'{half_sz:.8f}',
        'slTriggerPx':     f'{sl_price:.8f}',
        'slOrdPx':         '-1',
        'slTriggerPxType': 'last',
    })
    sl2_id = sl2_resp.get('data', [{}])[0].get('algoId', '')
    print(f'  [Trade] {symbol}: 2nd-half SL −{sl_pct}% (ID: {sl2_id}) ✓')

    # 4. Save to Supabase for monitor_option3_trades() to track
    saved = _save_option3_trade({
        'id':             oco_id,
        'symbol':         symbol,
        'entry_price':    price,
        'partial_tp_id':  oco_id,
        'sl_id':          oco_id,   # same ID — monitor uses fill px vs entry to tell TP vs SL
        'sl2_id':         sl2_id,   # 2nd-half SL — swapped for a trailing stop after TP
        'trailing_id':    '',       # set by the monitor when the TP fires
        'amount_usdt':    amt_usdt,
        'sz_half':        half_sz,
        'partial_tp_pct': tp_pct,
        'sl_pct':         sl_pct,
        'trailing_pct':   trail_pct,
        'phase':          1,
    })

    return {
        'amount_usdt': amt_usdt,
        'tp_pct':      tp_pct,
        'sl_pct':      sl_pct,
        'trail_pct':   trail_pct,
        'entry_price': price,
        'saved':       saved,   # False → monitor can't track it (no break-even move / exit alerts)
    }


# ── Supabase Option 3 trade helpers ──────────────────────────────────────────
def _fetch_option3_trades():
    """Fetch all active Option 3 trades (phase < 3) from Supabase."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        print('  [Supabase] URL or key not set — skipping trade fetch')
        return []
    try:
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/option3_trades?phase=lt.3&select=*',
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
        print(f'  [Supabase] Fetch error: HTTP {r.status_code} — {r.text[:300]}')
    except Exception as e:
        print(f'  [Supabase] Fetch failed: {e}')
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


def _get_order_fill_price(symbol, ord_id):
    """Return the average fill price of a regular OKX order, or None."""
    try:
        d = _okx_get(f'/api/v5/trade/order?instId={symbol}&ordId={ord_id}')
        for order in d.get('data', []):
            px = float(order.get('avgPx', '0') or '0')
            if px > 0:
                return px
    except Exception as e:
        print(f'  [Option3] Order fill lookup error ({ord_id}): {e}')
    return None


def _get_fill_price(algo_id, ord_type='conditional', symbol=None):
    """
    Return the actual fill price of a triggered OKX algo order.
    OKX often leaves avgPx empty on algo-history rows, so fall back to actualPx,
    then to the avgPx of the child market order the algo triggered (ordId).
    """
    try:
        d = _okx_get(f'/api/v5/trade/orders-algo-history?ordType={ord_type}&algoId={algo_id}')
        for order in d.get('data', []):
            if order.get('algoId') == algo_id and order.get('state') == 'effective':
                for key in ('avgPx', 'actualPx'):
                    px = float(order.get(key, '0') or '0')
                    if px > 0:
                        return px
                ord_id = order.get('ordId', '')
                inst   = symbol or order.get('instId', '')
                if ord_id and inst:
                    return _get_order_fill_price(inst, ord_id)
    except Exception:
        return None
    return None


def _is_algo_cancelled(algo_id, ord_type='conditional'):
    """Return True if an OKX algo order was manually cancelled or failed (not triggered)."""
    try:
        d = _okx_get(f'/api/v5/trade/orders-algo-history?ordType={ord_type}&algoId={algo_id}')
        for order in d.get('data', []):
            if order.get('algoId') == algo_id and order.get('state') in ('canceled', 'order_failed'):
                return True
    except Exception as e:
        print(f'  [Option3] Cancel check error ({algo_id}): {e}')
    return False


def _rank_candidate(sig, rsi_1h, rsi_4h, vol_ratio):
    """
    Composite ranking score for a STRONG BUY candidate.
    Used when multiple signals fire in the same scan to pick the top 2.

    Components (higher = better opportunity):
      sig.score  (4–9) : aggregate RSI/MACD/BB/4H/vol indicator checks — dominant factor
      RSI depth (+0–1) : how far 1H RSI is below 30 (RSI 15 beats RSI 29)
      4H depth  (+0–0.5): higher-timeframe alignment depth (multi-TF conviction)
      Volume    (+0–0.5): relative volume surge above average (confirms buying pressure)
    """
    score = float(sig['score'])
    if rsi_1h and rsi_1h < 30:
        score += (30 - rsi_1h) / 30.0        # max +1.0 when RSI→0
    if rsi_4h and rsi_4h < 40:
        score += (40 - rsi_4h) / 80.0        # max +0.5 when 4H RSI→0
    if vol_ratio and vol_ratio > 1.0:
        score += min(vol_ratio - 1.0, 2.0) / 4.0  # max +0.5 at vol_ratio=3
    return score


def _update_sl_to_breakeven(trade):
    """Cancel original SL and place a new conditional SL at entry price."""
    symbol   = trade['symbol']
    entry_px = float(trade['entry_price'])
    sl_id    = trade['sl_id']
    sz_half  = trade['sz_half']

    try:
        _okx_post('/api/v5/trade/cancel-algos', [{'algoId': sl_id, 'instId': symbol}])
        print(f'  [Option3] {symbol}: original SL ({sl_id}) canceled ✓')
    except Exception as e:
        print(f'  [Option3] {symbol}: SL cancel warning (may already be gone): {e}')

    try:
        resp      = _okx_post('/api/v5/trade/order-algo', {
            'instId':          symbol,
            'tdMode':          'cash',
            'side':            'sell',
            'ordType':         'conditional',
            'sz':              f'{float(sz_half):.8f}',
            'slTriggerPx':     f'{float(entry_px):.8f}',
            'slOrdPx':         '-1',
            'slTriggerPxType': 'last',
        })
        new_sl_id = resp.get('data', [{}])[0].get('algoId', '')
        print(f'  [Option3] {symbol}: break-even SL placed at {entry_px} (ID: {new_sl_id}) ✓')
        return new_sl_id
    except Exception as e:
        print(f'  [Option3] {symbol}: break-even SL placement failed: {e}')
        return None


def _mark_phase2(trade_id, updates):
    """Update Supabase: set phase=2 plus the phase-2 protection order IDs
    (e.g. {'trailing_id': ..., 'sl_id': ...})."""
    try:
        r = requests.patch(
            f'{SUPABASE_URL}/rest/v1/option3_trades?id=eq.{trade_id}',
            headers={
                'apikey':        SUPABASE_KEY,
                'Authorization': f'Bearer {SUPABASE_KEY}',
                'Content-Type':  'application/json',
            },
            json={'phase': 2, **updates},
            timeout=10,
        )
        if r.status_code in (200, 204):
            print(f'  [Option3] Trade {trade_id}: marked phase 2 ✓')
        else:
            print(f'  [Option3] Supabase phase update failed: {r.status_code}')
    except Exception as e:
        print(f'  [Option3] Supabase phase update error: {e}')


def _cancel_algo(symbol, algo_id):
    """Cancel an OKX algo order silently — already-gone is fine."""
    try:
        _okx_post('/api/v5/trade/cancel-algos', [{'algoId': algo_id, 'instId': symbol}])
        print(f'  [Option3] {symbol}: cancelled algo {algo_id} ✓')
    except Exception as e:
        print(f'  [Option3] {symbol}: cancel algo {algo_id} warning: {e}')


def _mark_trade_closed(trade_id, exit_reason=None, exit_price=None, net_pnl=None):
    """
    Mark a trade as phase=3 (fully closed) in Supabase, recording the outcome
    (exit_reason / exit_price / net_pnl_usdt / closed_at) when known. The outcome
    columns feed the daily circuit breaker and the AI's trade-history context.
    Falls back to a plain phase=3 update if the columns don't exist yet.
    """
    def _patch(payload):
        return requests.patch(
            f'{SUPABASE_URL}/rest/v1/option3_trades?id=eq.{trade_id}',
            headers={
                'apikey':        SUPABASE_KEY,
                'Authorization': f'Bearer {SUPABASE_KEY}',
                'Content-Type':  'application/json',
            },
            json=payload,
            timeout=10,
        )

    payload = {'phase': 3}
    if exit_reason:
        payload['exit_reason'] = exit_reason
        payload['closed_at']   = datetime.now(timezone.utc).isoformat()
        if exit_price is not None:
            payload['exit_price'] = exit_price
        if net_pnl is not None:
            payload['net_pnl_usdt'] = round(net_pnl, 4)
    try:
        r = _patch(payload)
        if r.status_code in (200, 204):
            print(f'  [Option3] Trade {trade_id}: marked closed ✓ ({exit_reason or "no outcome data"})')
            return
        if exit_reason:
            print(f'  [Option3] Close-with-outcome failed ({r.status_code}) — outcome columns '
                  f'missing? Run the SQL migration in docs/ARCHITECTURE.md. Retrying plain close...')
            r2 = _patch({'phase': 3})
            if r2.status_code in (200, 204):
                print(f'  [Option3] Trade {trade_id}: marked closed ✓ (outcome NOT recorded)')
                return
        print(f'  [Option3] Close update failed: {r.status_code}')
    except Exception as e:
        print(f'  [Option3] Close update error: {e}')


def _swap_sl2_to_trailing(trade):
    """
    After the partial TP fills (new-format trades): cancel the 2nd-half stop-loss and
    replace it with an immediately-active trailing stop. As long as trail% < TP%, the
    trailing floor sits above entry, so the 2nd half stays break-even-or-better.
    Returns ('trailing', algo_id) on success,
            ('be_sl',    algo_id) if the trailing failed but a break-even SL was placed,
            (None, '')            if the 2nd half could not be protected at all.
    """
    symbol    = trade['symbol']
    sz_half   = f"{float(trade['sz_half']):.8f}"   # never scientific notation — OKX rejects it
    trail_pct = float(trade.get('trailing_pct', 0) or 0)
    entry_px  = float(trade.get('entry_price', 0) or 0)

    _cancel_algo(symbol, trade['sl2_id'])

    try:
        resp = _okx_post('/api/v5/trade/order-algo', {
            'instId': symbol, 'tdMode': 'cash', 'side': 'sell',
            'ordType':       'move_order_stop',
            'sz':            sz_half,
            'callbackRatio': f'{trail_pct / 100:.4f}',
        })
        tid = resp.get('data', [{}])[0].get('algoId', '')
        if tid:
            print(f'  [Option3] {symbol}: trailing stop {trail_pct}% now active (ID: {tid}) ✓')
            return 'trailing', tid
    except Exception as e:
        print(f'  [Option3] {symbol}: trailing stop placement failed: {e}')

    # Fallback: at least pin the 2nd half to break-even
    try:
        resp = _okx_post('/api/v5/trade/order-algo', {
            'instId': symbol, 'tdMode': 'cash', 'side': 'sell',
            'ordType':         'conditional',
            'sz':              sz_half,
            'slTriggerPx':     f'{entry_px:.8f}',
            'slOrdPx':         '-1',
            'slTriggerPxType': 'last',
        })
        sid = resp.get('data', [{}])[0].get('algoId', '')
        if sid:
            print(f'  [Option3] {symbol}: fallback break-even SL placed (ID: {sid}) ✓')
            return 'be_sl', sid
    except Exception as e:
        print(f'  [Option3] {symbol}: fallback break-even SL failed: {e}')
    return None, ''


def _count_recent_sl(hours=24):
    """
    Circuit-breaker input: number of stop-loss exits in the last N hours.
    Returns 0 (fail-open, logged) if the outcome columns don't exist yet or
    Supabase is unreachable.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return 0
    # 'Z' suffix instead of '+00:00' — a literal '+' in a URL query decodes as a space
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/option3_trades'
            f'?select=id&exit_reason=eq.sl&closed_at=gte.{since}',
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
            timeout=10,
        )
        if r.status_code == 200:
            return len(r.json())
        print(f'  [Safety] SL-count query failed (HTTP {r.status_code}) — outcome columns '
              f'missing? Run the SQL migration in docs/ARCHITECTURE.md. Circuit breaker inactive.')
    except Exception as e:
        print(f'  [Safety] SL-count query error: {e} — circuit breaker inactive')
    return 0


def _trade_history_context(symbol):
    """
    Recent live trade outcomes formatted for the Claude Haiku prompt, so the AI can
    skip or downsize setups that have been losing. Returns '' when no data exists
    yet (or the outcome columns are missing) — the prompt simply omits the section.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return ''
    try:
        r = requests.get(
            f'{SUPABASE_URL}/rest/v1/option3_trades'
            f'?phase=eq.3&exit_reason=not.is.null&order=closed_at.desc&limit=20'
            f'&select=symbol,exit_reason,net_pnl_usdt,closed_at',
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
            timeout=10,
        )
        if r.status_code != 200:
            return ''
        rows = r.json()
        if not rows:
            return ''
        wins  = sum(1 for t in rows if float(t.get('net_pnl_usdt') or 0) > 0)
        total = sum(float(t.get('net_pnl_usdt') or 0) for t in rows)
        lines = [f'Last {len(rows)} closed trades overall: {wins} wins / '
                 f'{len(rows) - wins} losses, net {total:+.2f} USDT']
        coin_rows = [t for t in rows if t.get('symbol') == symbol][:5]
        if coin_rows:
            parts = [f"{t.get('exit_reason')} {float(t.get('net_pnl_usdt') or 0):+.2f}"
                     for t in coin_rows]
            lines.append(f'{symbol} most recent: ' + ' · '.join(parts))
        else:
            lines.append(f'{symbol}: no closed trades yet')
        return '\n'.join(lines)
    except Exception:
        return ''


def _phase1_pnl(trade):
    """
    Net USDT profit of the phase-1 partial TP exit, recovered from the OKX fill
    history (nothing is stored in Supabase). None if the fill can't be found.
    """
    entry_px = float(trade.get('entry_price', 0) or 0)
    sz_half  = float(trade.get('sz_half', '0') or 0)
    tp_id    = trade.get('partial_tp_id')
    if not tp_id or entry_px <= 0 or sz_half <= 0:
        return None
    tp_fill = _get_fill_price(tp_id, 'conditional', trade.get('symbol'))
    if tp_fill and tp_fill > entry_px:
        net, _, _, _ = _exit_pnl(entry_px, tp_fill, sz_half)
        return net
    return None


def _close_full_position_at_sl(trade, sl_fill_px):
    """
    After an SL trigger, close out the whole trade and report the exact USDT loss
    (both halves, incl. fees).
      New-format trades: the 2nd half has its own SL at the same trigger price, so
      OKX normally sells BOTH halves server-side — we just collect the fills.
      Legacy trades: cancel the dormant trailing stop and market-sell the 2nd half.
    sl_fill_px may be None — falls back to the SL trigger price as an estimate.
    """
    symbol   = trade['symbol']
    coin     = symbol.replace('-USDT', '')
    entry_px = float(trade.get('entry_price', 0) or 0)
    sz_half  = float(trade.get('sz_half', '0') or 0)
    sl_pct   = float(trade.get('sl_pct', 0) or 0)
    sl2_id   = trade.get('sl2_id') or ''

    trailing_id = trade.get('trailing_id')
    if trailing_id:
        _cancel_algo(symbol, trailing_id)   # legacy dormant trailing stop

    second_half_sold = False
    half2_px         = None

    if sl2_id:
        # New format: the 2nd-half SL shares the trigger price and should have fired
        # on OKX at (almost) the same moment. Give it a short grace period.
        if not _is_algo_triggered(sl2_id, 'conditional'):
            time.sleep(2)
        if _is_algo_triggered(sl2_id, 'conditional'):
            second_half_sold = True
            half2_px = _get_fill_price(sl2_id, 'conditional', symbol)
            print(f'  [Option3] {symbol}: 2nd-half SL fired server-side ✓')
        else:
            _cancel_algo(symbol, sl2_id)   # cancel first so the market sell can't double-sell

    if not second_half_sold:
        half2_ord_id = ''
        try:
            resp = _okx_post('/api/v5/trade/order', {
                'instId': symbol, 'tdMode': 'cash',
                'side': 'sell', 'ordType': 'market', 'sz': f'{sz_half:.8f}',
            })
            half2_ord_id     = resp.get('data', [{}])[0].get('ordId', '')
            second_half_sold = True
            print(f'  [Option3] {symbol}: remaining 50% market-sold ✓')
        except Exception as e:
            print(f'  [Option3] {symbol}: could not sell remaining 50%: {e}')
        if second_half_sold and half2_ord_id:
            time.sleep(1.0)   # let OKX register the market-sell fill
            half2_px = _get_order_fill_price(symbol, half2_ord_id)

    estimated = False
    if not sl_fill_px and entry_px > 0 and sl_pct > 0:
        sl_fill_px = entry_px * (1 - sl_pct / 100)   # SL trigger price
        estimated  = True
    if half2_px is None:
        half2_px = sl_fill_px

    net_total = avg_exit = None
    if sl_fill_px and entry_px > 0:
        exit_prices = [sl_fill_px] + ([half2_px] if second_half_sold else [])
        net_total = fee_total = 0.0
        for px in exit_prices:
            net, fees, _, _ = _exit_pnl(entry_px, px, sz_half)
            net_total += net
            fee_total += fees
        avg_exit = sum(exit_prices) / len(exit_prices)
        pct      = (avg_exit / entry_px - 1) * 100
        approx   = '~' if estimated else ''
        loss_str = f'{approx}{fmt_usdt(net_total)} USDT ({pct:+.1f}% incl. fees)'
        detail   = (f"📉 OKX fees paid: ${fee_total:.4f} USDT\n"
                    f"📍 Entry: {fmt_price(entry_px)} → Exit: {approx}{fmt_price(avg_exit)}")
    else:
        loss_str = f'−{sl_pct}% on full position'
        detail   = f"📍 Entry: {fmt_price(entry_px)}"

    _mark_trade_closed(trade['id'], 'sl', avg_exit, net_total)
    extra = '' if second_half_sold else '\n⚠️ Could not auto-sell remaining 50% — check OKX'
    send_telegram(
        f"🔴 <b>Stop Loss Hit — {coin}</b>\n"
        f"💸 Total loss: {loss_str}\n"
        f"{detail}\n"
        f"✅ Full position closed (both halves){extra}"
    )


# ── Option 3 exit monitor ─────────────────────────────────────────────────────
def monitor_option3_trades():
    """
    Poll all active Option 3 trades and send Telegram on every exit event.

    Phase 1: partial TP hit  → lock profit, move SL to break-even, advance to phase 2.
             SL hit (OCO)    → sell remaining 50%, report full loss, close trade.
    Phase 2: trailing stop   → report profit with exact USDT gain, close trade.
             break-even SL   → report zero-loss exit, close trade.
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
        sz_half  = float(trade.get('sz_half', '0') or 0)
        ptp_pct  = float(trade.get('partial_tp_pct', 0) or 0)
        coin     = symbol.replace('-USDT', '')

        try:
            if phase == 1:
                tp_id  = trade['partial_tp_id']
                sl_id  = trade.get('sl_id')
                is_oco = (tp_id and sl_id and tp_id == sl_id)

                if _is_algo_triggered(tp_id, 'conditional'):
                    fill_px  = _get_fill_price(tp_id, 'conditional', symbol)
                    tp_fired = (not is_oco) or (fill_px is not None and fill_px > entry_px)

                    if tp_fired:
                        # ── Partial TP filled ──────────────────────────────────
                        print(f'  [Option3] {symbol}: partial TP triggered ✓')
                        estimated = False
                        if not fill_px and entry_px > 0 and ptp_pct > 0:
                            fill_px   = entry_px * (1 + ptp_pct / 100)   # TP trigger price
                            estimated = True
                        tp_pnl = None
                        approx = ''
                        if fill_px and entry_px > 0:
                            tp_pnl, total_fees, buy_fee, sell_fee = _exit_pnl(entry_px, fill_px, sz_half)
                            approx      = '~' if estimated else ''
                            profit_line = f'{approx}{fmt_usdt(tp_pnl)} USDT (after fees)'
                            fee_line    = f'📉 OKX fees: ${total_fees:.4f} USDT (entry ${buy_fee:.4f} + exit ${sell_fee:.4f})'
                        else:
                            profit_line = f'+{ptp_pct}% on 50% of position'
                            fee_line    = None

                        sl2_id = trade.get('sl2_id') or ''
                        if sl2_id and _is_algo_triggered(sl2_id, 'conditional'):
                            # ── Whipsaw: TP filled, then price reversed and the 2nd-half
                            #    SL also fired before this monitor run — trade fully closed.
                            print(f'  [Option3] {symbol}: 2nd-half SL also fired (whipsaw) — closing...')
                            half2_px = _get_fill_price(sl2_id, 'conditional', symbol)
                            sl_pct_v = float(trade.get('sl_pct', 0) or 0)
                            est2 = estimated
                            if not half2_px and entry_px > 0 and sl_pct_v > 0:
                                half2_px, est2 = entry_px * (1 - sl_pct_v / 100), True
                            msg = [f"🔄 <b>Fast Reversal — {coin}</b>",
                                   f"✅ TP hit on first 50%: {profit_line}"]
                            whole = None
                            if half2_px and entry_px > 0 and tp_pnl is not None:
                                pnl2, _, _, _ = _exit_pnl(entry_px, half2_px, sz_half)
                                a2 = '~' if est2 else ''
                                whole = tp_pnl + pnl2
                                msg.append(f"🔴 Price reversed — 2nd half stopped out: {a2}{fmt_usdt(pnl2)} USDT")
                                msg.append(f"📊 Whole trade net result: {a2}{fmt_usdt(whole)} USDT")
                            else:
                                msg.append(f"🔴 Price reversed — 2nd half stopped out")
                            _mark_trade_closed(trade['id'], 'tp_then_sl', half2_px, whole)
                            send_telegram('\n'.join(msg))
                        else:
                            # ── Normal TP: protect the 2nd half for phase 2 ────────
                            if sl2_id:
                                kind, oid = _swap_sl2_to_trailing(trade)
                            else:
                                # Legacy trade (pre-sl2 format): dormant trailing already
                                # armed at the TP price — add a break-even SL beside it.
                                oid  = _update_sl_to_breakeven(trade)
                                kind = 'legacy_be' if oid else 'legacy_none'
                            trail_v = float(trade.get('trailing_pct', 0) or 0)
                            if kind == 'trailing':
                                _mark_phase2(trade['id'], {'trailing_id': oid, 'sl_id': ''})
                                guard_lines = [f"🔄 Trailing stop now active on remaining 50% — "
                                               f"exits on first {trail_v}% pullback from the peak"]
                            elif kind == 'be_sl':
                                _mark_phase2(trade['id'], {'trailing_id': '', 'sl_id': oid})
                                guard_lines = [f"⚠️ Trailing stop failed — 2nd half protected by a "
                                               f"break-even SL instead (check OKX)"]
                            elif kind == 'legacy_be':
                                _mark_phase2(trade['id'], {'sl_id': oid})
                                guard_lines = [f"🛡️ SL moved to entry (break-even) — 2nd half is now risk-free",
                                               f"🔄 Trailing stop protecting remaining 50%"]
                            elif kind == 'legacy_none':
                                _mark_phase2(trade['id'], {'sl_id': ''})
                                guard_lines = [f"⚠️ Break-even SL could not be placed — check OKX",
                                               f"🔄 Trailing stop protecting remaining 50%"]
                            else:
                                # New-format trade with BOTH protections failed — don't
                                # leave a stuck row; tell the user to manage it manually.
                                _mark_trade_closed(trade['id'], 'error')
                                guard_lines = [f"🚨 Could not place trailing stop OR break-even SL — "
                                               f"2nd half is UNPROTECTED. Manage it manually on OKX!"]
                            msg_parts = [
                                f"✅ <b>Partial TP Hit — {coin}</b>",
                                f"💰 Profit locked: {profit_line}",
                            ]
                            if fee_line:
                                msg_parts.append(fee_line)
                            msg_parts += guard_lines
                            send_telegram('\n'.join(msg_parts))
                    else:
                        # ── SL side of OCO fired ───────────────────────────────
                        print(f'  [Option3] {symbol}: SL triggered (OCO) — closing full position...')
                        _close_full_position_at_sl(trade, fill_px)

                # Old format: separate SL order (backwards compat)
                elif not is_oco and sl_id and _is_algo_triggered(sl_id, 'conditional'):
                    fill_px = _get_fill_price(sl_id, 'conditional', symbol)
                    _cancel_algo(symbol, tp_id)
                    _close_full_position_at_sl(trade, fill_px)

                elif _is_algo_cancelled(tp_id, 'conditional'):
                    # ── OCO order manually cancelled on OKX ───────────────────────
                    print(f'  [Option3] {symbol}: OCO cancelled — marking trade closed...')
                    trailing_id = trade.get('trailing_id')
                    if trailing_id:
                        _cancel_algo(symbol, trailing_id)
                    sl2_id = trade.get('sl2_id') or ''
                    if sl2_id:
                        _cancel_algo(symbol, sl2_id)
                    _mark_trade_closed(trade['id'], 'cancelled')
                    send_telegram(
                        f"⚠️ <b>Orders Cancelled — {coin}</b>\n"
                        f"📋 OCO order was manually cancelled on OKX\n"
                        f"🔄 Remaining protective orders also cancelled\n"
                        f"📌 Trade marked closed — new {coin} signals will trigger fresh trades"
                    )
                else:
                    print(f'  [Option3] {symbol}: phase 1 — waiting for TP or SL')

            elif phase == 2:
                trailing_id = trade.get('trailing_id')
                be_sl_id    = trade.get('sl_id')  # updated to break-even SL in _mark_phase2

                if trailing_id and _is_algo_triggered(trailing_id, 'move_order_stop'):
                    fill_px   = _get_fill_price(trailing_id, 'move_order_stop', symbol)
                    estimated = False
                    if not fill_px:
                        t = fetch_ticker(symbol)   # last resort: current market price
                        if t:
                            fill_px, estimated = t['price'], True
                    fee_line    = total_line = None
                    phase1_line = "✅ Phase 1 profit already secured"
                    whole       = None
                    if fill_px and entry_px > 0:
                        net_pnl, total_fees, buy_fee, sell_fee = _exit_pnl(entry_px, fill_px, sz_half)
                        gain_pct   = (fill_px / entry_px - 1) * 100
                        approx     = '~' if estimated else ''
                        profit_str = f'{approx}{fmt_usdt(net_pnl)} USDT ({gain_pct:+.1f}%) after fees'
                        fee_line   = f'📉 OKX fees: ${total_fees:.4f} USDT (entry ${buy_fee:.4f} + exit ${sell_fee:.4f})'
                        p1_pnl     = _phase1_pnl(trade)
                        whole      = net_pnl + p1_pnl if p1_pnl is not None else net_pnl
                        if p1_pnl is not None:
                            phase1_line = f"✅ Phase 1 profit already secured: {fmt_usdt(p1_pnl)} USDT"
                            total_line  = f"📊 Whole trade net result: {approx}{fmt_usdt(whole)} USDT"
                    else:
                        profit_str = 'exited via trailing stop'
                    if be_sl_id:
                        _cancel_algo(symbol, be_sl_id)   # remove the now-orphaned break-even SL
                    _mark_trade_closed(trade['id'], 'tp_trail', fill_px, whole)
                    msg_parts = [
                        f"🏁 <b>Trade Closed — {coin}</b>",
                        f"🔄 Trailing stop exit: {profit_str}",
                    ]
                    if fee_line:
                        msg_parts.append(fee_line)
                    msg_parts.append(phase1_line)
                    if total_line:
                        msg_parts.append(total_line)
                    send_telegram('\n'.join(msg_parts))

                elif be_sl_id and _is_algo_triggered(be_sl_id, 'conditional'):
                    fill_px   = _get_fill_price(be_sl_id, 'conditional', symbol)
                    estimated = False
                    if not fill_px and entry_px > 0:
                        fill_px, estimated = entry_px, True   # BE SL triggers at entry price
                    half_line   = "🛡️ Break-even SL hit — 2nd half exited at entry price"
                    phase1_line = "✅ Phase 1 profit is secured — net result is positive"
                    total_line  = None
                    whole       = None
                    if fill_px and entry_px > 0:
                        net_pnl, _, _, _ = _exit_pnl(entry_px, fill_px, sz_half)
                        approx    = '~' if estimated else ''
                        half_line = f"🛡️ Break-even SL hit — 2nd half exited at entry: {approx}{fmt_usdt(net_pnl)} USDT (after fees)"
                        p1_pnl    = _phase1_pnl(trade)
                        whole     = net_pnl + p1_pnl if p1_pnl is not None else net_pnl
                        if p1_pnl is not None:
                            phase1_line = f"✅ Phase 1 profit secured: {fmt_usdt(p1_pnl)} USDT"
                            total_line  = f"📊 Whole trade net result: {approx}{fmt_usdt(whole)} USDT"
                    if trailing_id:
                        _cancel_algo(symbol, trailing_id)   # trailing never activated — remove it
                    _mark_trade_closed(trade['id'], 'break_even', fill_px, whole)
                    msg_parts = [
                        f"⚪ <b>Break-Even Exit — {coin}</b>",
                        half_line,
                        phase1_line,
                    ]
                    if total_line:
                        msg_parts.append(total_line)
                    send_telegram('\n'.join(msg_parts))

                elif (trailing_id and _is_algo_cancelled(trailing_id, 'move_order_stop')) or \
                     (be_sl_id and _is_algo_cancelled(be_sl_id, 'conditional')):
                    # ── Phase 2 orders manually cancelled on OKX ──────────────────
                    print(f'  [Option3] {symbol}: phase 2 orders cancelled — marking trade closed...')
                    _mark_trade_closed(trade['id'], 'cancelled')
                    send_telegram(
                        f"⚠️ <b>Orders Cancelled — {coin}</b>\n"
                        f"📋 Trailing stop / break-even SL was manually cancelled on OKX\n"
                        f"📌 Trade marked closed — new {coin} signals will trigger fresh trades"
                    )
                else:
                    print(f'  [Option3] {symbol}: phase 2 — waiting for trailing stop or break-even SL')

        except Exception as e:
            print(f'  [Option3] {symbol}: error — {e}')


# ── Single scan ───────────────────────────────────────────────────────────────
MAX_TRADES_PER_SCAN = 1 if TEST_MODE else 2  # hard cap: never place more than this many trades in one scan

# Safety rails — enforced in production, logged-only in TEST_MODE so tests keep flowing.
MAX_OPEN_TRADES = 3   # never hold more than this many Option 3 trades at once
MAX_SL_PER_DAY  = 3   # circuit breaker: pause new trades after this many stop-losses in 24h


def run_scan(cache, warm_up=False):
    """
    warm_up=True → cache was empty on this GitHub Actions run.
    Populate state without sending alerts or placing any trades.

    Trade placement uses a two-pass approach when multiple STRONG BUY signals fire:
      Pass 1 — scan all coins, collect every qualified STRONG BUY into `candidates`.
      Pass 2 — rank by composite score, place top MAX_TRADES_PER_SCAN trades only.
               Balance is refreshed between the 1st and 2nd trade so the 2nd trade
               sizes itself off the actual remaining capital.
      Pass 3 — send all Telegram alerts (including 'cap' notices for skipped coins).
    """
    now = time.time()

    active_trades  = _fetch_option3_trades()
    active_symbols = {t['symbol'] for t in active_trades}

    usdt_balance = 0.0
    if OKX_API_KEY and (CLAUDE_API_KEY or TEST_MODE) and not warm_up:
        usdt_balance = _fetch_usdt_balance()
        if usdt_balance > 0:
            print(f'  [AutoTrade] Available USDT: ${usdt_balance:.2f}')
        else:
            print(f'  [AutoTrade] Balance unavailable — auto-trade disabled this run')

    # ── Pass 1: scan all coins, collect signal data ──────────────────────────
    pending_alerts = []  # (symbol, sig, ticker, trade_result, cache_update)
    candidates     = []  # STRONG BUY coins eligible for auto-trade, to be ranked

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

            candle_30m = fetch_candles(symbol, bar='30m', limit=50)
            if candle_30m:
                r_opens, r_closes, r_volumes = candle_30m['opens'], candle_30m['closes'], candle_30m['volumes']
            else:
                r_opens, r_closes, r_volumes = opens, closes, volumes

            candle_4h = fetch_candles(symbol, bar='4H', limit=50)
            rsi_4h    = calc_rsi(candle_4h['closes']) if candle_4h and len(candle_4h['closes']) > 14 else None

            rsi_1h    = calc_rsi(closes)
            macd_data = calc_macd(closes)
            bb_data   = calc_bb(closes)
            sig       = generate_signal(rsi_1h, macd_data, bb_data, vol_ratio, rsi_4h)

            if TEST_FORCE_SIGNAL and symbol == 'BTC-USDT':
                sig = {'label': 'STRONG BUY', 'score': 5.0, 'reasons': ['TEST — forced signal, not real']}

            label = sig['label']
            zone  = direction_zone(label)

            prev         = cache.get(symbol, {})
            alerted_zone = prev.get('alerted_zone')
            alerted_at   = prev.get('alerted_at', 0)

            cache[symbol] = {**prev, 'label': label, 'zone': zone}

            if warm_up:
                cache[symbol] = {'label': label, 'zone': zone, 'alerted_zone': zone, 'alerted_at': now}
                print(f'  {symbol}: {label} — warm-up, state saved, no alert')
                time.sleep(0.1)
                continue

            print(f'  {symbol}: {label} (zone={zone}, last_alerted={alerted_zone or "—"})')

            if zone == 'neutral':
                time.sleep(0.3)
                continue

            if label != 'STRONG BUY':
                time.sleep(0.3)
                continue

            if not TEST_MODE and not reversal_confirmed(r_opens, r_closes, r_volumes, zone):
                print(f'  {symbol}: STRONG BUY — no 30min reversal confirmation yet')
                time.sleep(0.3)
                continue

            if zone == alerted_zone:
                secs_in_zone = now - alerted_at
                if secs_in_zone < REZONE_REMINDER:
                    print(f'  {symbol}: still in {zone} zone ({int(secs_in_zone/60)}m) — suppressed')
                    time.sleep(0.3)
                    continue
                print(f'  {symbol}: still in {zone} zone for {int(secs_in_zone/3600)}h — reminder alert')

            if now - alerted_at < FLIP_COOLDOWN:
                secs_left = int(FLIP_COOLDOWN - (now - alerted_at))
                print(f'  {symbol}: zone flip within cooldown ({secs_left}s left) — suppressed')
                time.sleep(0.3)
                continue

            # Coin passed all filters — build cache update once
            cache_update = {**cache[symbol], 'alerted_zone': zone, 'alerted_at': now}

            if symbol in active_symbols:
                # Already has a running trade — signal alert only, no new trade
                print(f'  {symbol}: STRONG BUY — active trade already running, signal only')
                pending_alerts.append((symbol, sig, ticker, None, cache_update))
            else:
                # Eligible for auto-trade — defer to ranking pass
                candidates.append({
                    'symbol':       symbol,
                    'sig':          sig,
                    'ticker':       ticker,
                    'rsi_1h':       rsi_1h,
                    'rsi_4h':       rsi_4h,
                    'macd_data':    macd_data,
                    'bb_data':      bb_data,
                    'vol_ratio':    vol_ratio,
                    'rank_score':   _rank_candidate(sig, rsi_1h, rsi_4h, vol_ratio),
                    'cache_update': cache_update,
                })

        except Exception as e:
            print(f'  {symbol}: ERROR — {e}')

    # ── Pass 2: rank candidates, place top MAX_TRADES_PER_SCAN auto-trades ───
    # TEST_MODE safety: keep only ONE test trade alive at a time. If a trade is
    # already running, don't open another — just refresh the cache (no Telegram).
    if TEST_MODE and active_symbols and candidates:
        print(f'  [TEST MODE] {len(active_symbols)} active trade(s) — '
              f'holding off on new test trades until they close')
        for cand in candidates:
            pending_alerts.append((cand['symbol'], cand['sig'], cand['ticker'], None, cand['cache_update']))
        candidates = []

    # ── Safety rails: BTC regime, open-trade cap, daily SL circuit breaker ────
    # Enforced in production; in TEST_MODE they are evaluated and logged only,
    # so the test pipeline keeps producing trades regardless of market regime.
    if candidates:
        regime_ok, regime_msg = btc_regime_ok()
        print(f'  [Regime] {regime_msg}')

        block_reason = None
        if not regime_ok:
            block_reason = 'bearish BTC regime'
        elif len(active_trades) >= MAX_OPEN_TRADES:
            block_reason = f'{len(active_trades)} trades already open (cap: {MAX_OPEN_TRADES})'
        else:
            recent_sl = _count_recent_sl(24)
            if recent_sl >= MAX_SL_PER_DAY:
                block_reason = f'{recent_sl} stop-losses in the last 24h (circuit breaker)'
                cb = cache.get('_circuit_breaker', {})
                if not TEST_MODE and now - cb.get('alerted_at', 0) > 24 * 3600:
                    send_telegram(
                        f"⏸️ <b>Auto-Trading Paused</b>\n"
                        f"🔴 {recent_sl} stop-losses hit in the last 24h — circuit breaker active\n"
                        f"▶️ New trades resume automatically once the 24h window clears"
                    )
                    cache['_circuit_breaker'] = {'alerted_at': now}

        if block_reason:
            if TEST_MODE:
                print(f'  [Safety] {block_reason} — TEST MODE: logged only, trading anyway')
            else:
                print(f'  [Safety] No new trades this scan: {block_reason}')
                for cand in candidates:
                    pending_alerts.append((cand['symbol'], cand['sig'], cand['ticker'], None, cand['cache_update']))
                candidates = []

    if candidates:
        candidates.sort(key=lambda x: x['rank_score'], reverse=True)

        top_candidates     = candidates[:MAX_TRADES_PER_SCAN]
        skipped_candidates = candidates[MAX_TRADES_PER_SCAN:]

        print(f'\n  [AutoTrade] {len(candidates)} signal(s) qualified — '
              f'trading top {len(top_candidates)}, skipping {len(skipped_candidates)}')
        for i, c in enumerate(candidates):
            print(f'    #{i+1}  {c["symbol"]}  score={c["rank_score"]:.2f}'
                  f'  ({"TRADE" if i < MAX_TRADES_PER_SCAN else "skip"})')

        for rank_i, cand in enumerate(top_candidates):
            symbol = cand['symbol']

            # Refresh live balance after the first trade — 2nd trade sizes off real remainder
            if rank_i > 0:
                usdt_balance = _fetch_usdt_balance()
                print(f'  [AutoTrade] Balance refreshed after trade #{rank_i}: ${usdt_balance:.2f} USDT')

            trade_result = None
            if TEST_MODE and OKX_API_KEY and usdt_balance >= MIN_TRADE_USDT:
                # Test mode: bypass the AI advisor, place a fixed tiny trade so the
                # full Option 3 pipeline (buy → OCO → trailing → monitor → Telegram) runs.
                params = {
                    'amount_usdt':    min(TEST_TRADE_USDT, usdt_balance),
                    'partial_tp_pct': TEST_TP_PCT,
                    'sl_pct':         TEST_SL_PCT,
                    'trailing_pct':   TEST_TRAIL_PCT,
                }
                print(f'  {symbol}: [TEST MODE] placing fixed ${params["amount_usdt"]:.2f} trade (AI bypassed)...')
                try:
                    trade_result = place_option3_trade(symbol, params, cand['ticker'])
                    print(f'  {symbol}: [TEST MODE] Option 3 trade placed ✓')
                except Exception as e:
                    print(f'  {symbol}: trade placement failed — {e}')
                    trade_result = 'error'
            elif not TEST_MODE and OKX_API_KEY and CLAUDE_API_KEY and usdt_balance >= MIN_TRADE_USDT:
                print(f'  {symbol}: asking Claude for trade params '
                      f'(rank #{rank_i + 1}/{len(top_candidates)}, score={cand["rank_score"]:.2f})...')
                params = ai_trade_params(
                    symbol, cand['sig'], cand['ticker'], usdt_balance,
                    cand['rsi_1h'], cand['rsi_4h'], cand['macd_data'], cand['bb_data'], cand['vol_ratio'],
                )
                if params:
                    try:
                        trade_result = place_option3_trade(symbol, params, cand['ticker'])
                        print(f'  {symbol}: Option 3 trade placed ✓  (rank #{rank_i + 1})')
                    except Exception as e:
                        print(f'  {symbol}: trade placement failed — {e}')
                        trade_result = 'error'
                else:
                    trade_result = 'skip'

            pending_alerts.append((symbol, cand['sig'], cand['ticker'], trade_result, cand['cache_update']))

        for i, cand in enumerate(skipped_candidates):
            rank_num = len(top_candidates) + i + 1
            print(f'  {cand["symbol"]}: STRONG BUY ranked #{rank_num} — skipped (cap={MAX_TRADES_PER_SCAN})')
            pending_alerts.append((cand['symbol'], cand['sig'], cand['ticker'], 'cap', cand['cache_update']))

    # ── Pass 3: send Telegram only when a trade was actually placed ───────────
    # skip, cap, error, and signal-only (None) get no notification —
    # the user only wants to hear about confirmed new trades.
    for symbol, sig, ticker, trade_result, cache_update in pending_alerts:
        if isinstance(trade_result, dict):
            send_telegram(format_alert(symbol, sig, ticker, trade_result))
        cache[symbol] = cache_update
        time.sleep(0.3)


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    cache    = load_cache()
    fresh    = not bool(cache)
    start    = time.time()
    scan_num = 0

    if fresh:
        print('Cache is empty — first scan will populate state only (no alerts, no trades).')

    while True:
        scan_num += 1
        elapsed = time.time() - start
        print(f'\n=== Scan #{scan_num} | +{elapsed:.0f}s | {time.strftime("%H:%M:%S UTC")} ===')

        run_scan(cache, warm_up=(fresh and scan_num == 1))
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
