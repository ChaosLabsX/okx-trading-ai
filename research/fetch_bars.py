"""Fetch plain OHLCV bars at one interval for the whole universe, for long-history research
(years of 1H/4H/1D bars, where fetch_1m.py's minute data would be gigabytes).
Binance spot via the public data mirror, as everywhere in research/ (OKX is unreachable here).

usage: fetch_bars.py INTERVAL "YYYY-MM-DD" "YYYY-MM-DD" [OUTDIR]
       fetch_bars.py 1h 2024-11-01 2026-11-10        -> research/bars_1h/<SYM>.npy  (float64, n x 6)
Columns: open time (ms), open, high, low, close, quote volume (USDT). Only CLOSED bars are kept.
"""
import calendar, os, sys, time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # repo root
import signal_checker as sc

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = 'https://data-api.binance.vision/api/v3/klines'
STEP = {'1m': 60_000, '5m': 300_000, '15m': 900_000, '30m': 1_800_000, '1h': 3_600_000,
        '4h': 14_400_000, '1d': 86_400_000}
# Coins the live bot can no longer trade but which belong in a long history: without them
# every long-only result is flattered (only today's survivors would be in the sample).
EXTRA = ['ATOM-USDT', 'WIF-USDT', 'FLOKI-USDT', 'STRK-USDT', 'RUNE-USDT', 'TON-USDT', 'MATIC-USDT',
         'EOS-USDT', 'FTM-USDT', 'MKR-USDT', 'TRUMP-USDT']

iv = sys.argv[1]
utc = lambda s: calendar.timegm(time.strptime(s, '%Y-%m-%d')) * 1000
T0, T1 = utc(sys.argv[2]), utc(sys.argv[3])
OUT = os.path.join(HERE, sys.argv[4] if len(sys.argv) > 4 else f'bars_{iv}')
os.makedirs(OUT, exist_ok=True)
sess = requests.Session()


def get(params):
    for attempt in range(10):
        try:
            r = sess.get(BASE, params=params, timeout=20)
            if r.status_code in (418, 429):
                time.sleep(int(r.headers.get('Retry-After', 5)) + 1); continue
            if r.status_code == 400:
                return None                       # symbol not on Binance
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(2 + attempt)
    raise RuntimeError(f'failed {params}')


def fetch(inst):
    sym = inst.replace('-', '')
    fp = os.path.join(OUT, f'{sym}.npy')
    if os.path.exists(fp):
        return inst, 'cached'
    rows, t, step = [], T0, STEP[iv]
    while t < T1:
        b = get({'symbol': sym, 'interval': iv, 'startTime': t, 'endTime': T1 - 1, 'limit': 1000})
        if b is None:
            return inst, 'missing'
        if not b:
            t += 1000 * step; continue
        rows.extend(b)
        t = b[-1][0] + step
    now = int(time.time() * 1000)
    rows = [r for r in rows if r[0] + step <= now]          # closed bars only
    if not rows:
        return inst, 'empty'
    a = np.array([[r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])] for r in rows])
    np.save(fp, a)
    return inst, f'ok {len(a)} bars from {time.strftime("%Y-%m-%d", time.gmtime(a[0, 0] / 1000))}'


if __name__ == '__main__':
    t0 = time.time()
    universe = list(dict.fromkeys(list(sc.SYMBOLS) + EXTRA))
    if len(sys.argv) > 5:                           # optional: only these coins, e.g. BTC,ETH,SOL
        universe = [f'{c}-USDT' for c in sys.argv[5].split(',')]
    with ThreadPoolExecutor(6) as ex:
        for inst, status in ex.map(fetch, universe):
            print(f'{time.time() - t0:5.0f}s  {inst:<12} {status}', flush=True)
    print('done', flush=True)
