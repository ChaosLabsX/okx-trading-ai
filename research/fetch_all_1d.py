"""Daily bars for EVERY Binance USDT spot pair (not just the bot's list), so a momentum test
can use a point-in-time universe: "the N most traded coins as of that day", instead of a
list picked in 2026 by someone who already knew which coins had done well.

usage: fetch_all_1d.py 2023-01-01 2026-11-10      -> research/bars_all_1d/<SYM>.npy
"""
import calendar, os, sys, time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'bars_all_1d')
os.makedirs(OUT, exist_ok=True)
API = 'https://data-api.binance.vision/api/v3'
D = 86_400_000
utc = lambda s: calendar.timegm(time.strptime(s, '%Y-%m-%d')) * 1000
T0, T1 = utc(sys.argv[1]), utc(sys.argv[2])
sess = requests.Session()

# Not coins: stablecoins, fiat, wrapped/staked copies of another coin, tokenised gold.
NOT_COINS = set('USDC FDUSD TUSD DAI USDP BUSD UST USTC EUR EURI AEUR GBP TRY BRL PAX SUSD USDD PYUSD '
                'WBTC WBETH BETH BNSOL PAXG XAUT USD1 XUSD BFUSD RLUSD USDE FRAX LUSD GUSD'.split())


def get(path, params):
    for attempt in range(10):
        try:
            r = sess.get(API + path, params=params, timeout=30)
            if r.status_code in (418, 429):
                time.sleep(int(r.headers.get('Retry-After', 5)) + 1); continue
            if r.status_code == 400:
                return None
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(2 + attempt)
    raise RuntimeError(f'failed {path} {params}')


def fetch(sym):
    fp = os.path.join(OUT, f'{sym}.npy')
    if os.path.exists(fp):
        return sym, 'cached'
    rows, t = [], T0
    while t < T1:
        b = get('/klines', {'symbol': sym, 'interval': '1d', 'startTime': t, 'endTime': T1 - 1, 'limit': 1000})
        if not b:
            break
        rows.extend(b)
        t = b[-1][0] + D
    now = int(time.time() * 1000)
    rows = [r for r in rows if r[0] + D <= now]
    if len(rows) < 30:
        return sym, 'too short'
    np.save(fp, np.array([[r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])] for r in rows]))
    return sym, f'ok {len(rows)}'


if __name__ == '__main__':
    info = get('/exchangeInfo', {})
    syms = []
    for s in info['symbols']:
        base = s['baseAsset']
        if s['quoteAsset'] != 'USDT' or base in NOT_COINS:
            continue
        if base.endswith(('UP', 'DOWN', 'BULL', 'BEAR')) and len(base) > 4:
            continue
        syms.append(s['symbol'])
    print(f'{len(syms)} USDT pairs (all statuses)', flush=True)
    t0, n_ok = time.time(), 0
    with ThreadPoolExecutor(8) as ex:
        for sym, status in ex.map(fetch, syms):
            n_ok += status.startswith(('ok', 'cached'))
    print(f'done: {n_ok} saved in {time.time() - t0:.0f}s', flush=True)
