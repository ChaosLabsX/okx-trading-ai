"""Fetch Binance spot klines (1m for the window, 30m/1H/4H with warm-up) for the OKX universe.
OKX itself is DNS-blocked on this network; Binance's public data mirror is not.
usage: fetch_1m.py OUTDIR "YYYY-MM-DD HH:MM" "YYYY-MM-DD HH:MM"   (UTC start, end)"""
import array, calendar, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # repo root
import signal_checker as sc

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, sys.argv[1])
os.makedirs(OUT, exist_ok=True)
BASE = 'https://data-api.binance.vision/api/v3/klines'
MIN, H = 60_000, 3_600_000
utc = lambda s: calendar.timegm(time.strptime(s, '%Y-%m-%d %H:%M')) * 1000
W_START, NOW = utc(sys.argv[2]), utc(sys.argv[3])
sess = requests.Session()


def get(params):
    for attempt in range(10):
        try:
            r = sess.get(BASE, params=params, timeout=20)
            if r.status_code in (418, 429):
                time.sleep(int(r.headers.get('Retry-After', 5)) + 1); continue
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(2 + attempt)
    raise RuntimeError(f'failed {params}')


def klines(sym, interval, start, end, step):
    rows, t = [], start
    while t < end:
        batch = get({'symbol': sym, 'interval': interval, 'startTime': t, 'endTime': end - 1, 'limit': 1000})
        if not batch:
            t += 1000 * step; continue
        rows.extend(batch)
        t = batch[-1][0] + step
    return rows


def fetch_coin(inst):
    sym = inst.replace('-', '')
    fp = os.path.join(OUT, f'{sym}.json')
    if os.path.exists(fp):
        return inst, 'cached'
    try:
        get({'symbol': sym, 'interval': '1h', 'limit': 1})
    except Exception:
        return inst, 'missing'
    out = {}
    for iv, step, warm in (('1h', H, 160 * H), ('30m', 1_800_000, 60 * 1_800_000), ('4h', 4 * H, 120 * 4 * H)):
        rows = klines(sym, iv, W_START - warm, NOW, step)
        out[iv] = [[r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in rows]
    rows = klines(sym, '1m', W_START, NOW, MIN)
    n = (NOW - W_START) // MIN
    o, h, l, c, v = (array.array('d', [0.0]) * n for _ in range(5))
    have = bytearray(n)
    for r in rows:
        i = (r[0] - W_START) // MIN
        if 0 <= i < n:
            o[i], h[i], l[i], c[i], v[i] = (float(x) for x in r[1:6]); have[i] = 1
    last = None
    for i in range(n):                         # forward-fill gaps: flat candle, zero volume
        if have[i]:
            last = c[i]
        elif last is not None:
            o[i] = h[i] = l[i] = c[i] = last
    for name, arr in (('o', o), ('h', h), ('l', l), ('c', c), ('v', v)):
        with open(os.path.join(OUT, f'{sym}.1m.{name}'), 'wb') as f:
            arr.tofile(f)
    out['meta'] = {'w_start': W_START, 'now': NOW, 'n': n, 'gaps': n - sum(have)}
    with open(fp, 'w') as f:
        json.dump(out, f)
    return inst, f'ok gaps={n - sum(have)}'


if __name__ == '__main__':
    t0 = time.time()
    with ThreadPoolExecutor(4) as ex:
        for inst, status in ex.map(fetch_coin, list(sc.SYMBOLS)):
            print(f'{time.time() - t0:6.0f}s  {inst:<12} {status}', flush=True)
    print('done', flush=True)
