"""Futures-market history for the event studies in lab_exp5.py: open interest, long/short
ratios and taker buy/sell ratio (5-minute "metrics" files, one per symbol per day) and
funding rates (one file per symbol per month), from Binance's public archive.

usage: fetch_derivs.py 2024-01-01 2026-11-09 [OUTDIR=derivs]
       -> research/derivs/<SYM>.npy   hourly rows: t, oi_usd, top_ls_ratio, ls_ratio, taker_ratio
       -> research/derivs/<SYM>.fund.npy   rows: t, funding_rate
"""
import calendar, csv, io, os, sys, time, zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, sys.argv[3] if len(sys.argv) > 3 else 'derivs')
os.makedirs(OUT, exist_ok=True)
BASE = 'https://data.binance.vision/data/futures/um'
H, D = 3_600_000, 86_400_000
SYMS = ('BTC ETH BNB SOL XRP ADA DOGE TRX LTC BCH AVAX SUI NEAR APT OP ARB DOT LINK UNI AAVE '
        'INJ FIL TIA SEI ENA ONDO WLD RENDER LDO CRV').split()
utc = lambda s: calendar.timegm(time.strptime(s, '%Y-%m-%d')) * 1000
T0, T1 = utc(sys.argv[1]), utc(sys.argv[2])
sess = requests.Session()
adapter = requests.adapters.HTTPAdapter(pool_connections=64, pool_maxsize=64)
sess.mount('https://', adapter)


def grab(url):
    for attempt in range(6):
        try:
            r = sess.get(url, timeout=30)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            z = zipfile.ZipFile(io.BytesIO(r.content))
            return z.read(z.namelist()[0]).decode()
        except Exception:
            time.sleep(1 + attempt)
    return None


def metrics_day(args):
    sym, day = args
    ds = time.strftime('%Y-%m-%d', time.gmtime(day / 1000))
    txt = grab(f'{BASE}/daily/metrics/{sym}USDT/{sym}USDT-metrics-{ds}.zip')
    if not txt:
        return sym, day, None
    rows = {}
    for r in csv.DictReader(io.StringIO(txt)):
        try:
            t = calendar.timegm(time.strptime(r['create_time'][:19], '%Y-%m-%d %H:%M:%S')) * 1000
            hr = (t - 1) // H * H                         # the hour this 5-minute stamp closes into
            rows[hr] = (float(r['sum_open_interest_value'] or 'nan'),
                        float(r['count_toptrader_long_short_ratio'] or 'nan'),
                        float(r['count_long_short_ratio'] or 'nan'),
                        float(r['sum_taker_long_short_vol_ratio'] or 'nan'))   # last stamp of each hour wins
        except Exception:
            continue
    return sym, day, rows


def funding_month(args):
    sym, ym = args
    txt = grab(f'{BASE}/monthly/fundingRate/{sym}USDT/{sym}USDT-fundingRate-{ym}.zip')
    out = []
    if txt:
        for r in csv.DictReader(io.StringIO(txt)):
            try:
                out.append((int(r['calc_time']), float(r['last_funding_rate'])))
            except Exception:
                continue
    return sym, out


if __name__ == '__main__':
    t0 = time.time()
    days = list(range(T0, T1 + 1, D))
    todo = [s for s in SYMS if not os.path.exists(os.path.join(OUT, f'{s}.npy'))]
    acc = {s: {} for s in todo}
    jobs = [(s, d) for s in todo for d in days]
    done = 0
    with ThreadPoolExecutor(48) as ex:
        for sym, day, rows in ex.map(metrics_day, jobs):
            done += 1
            if rows:
                acc[sym].update(rows)
            if done % 3000 == 0:
                print(f'{time.time() - t0:5.0f}s  metrics {done}/{len(jobs)}', flush=True)
    for s in todo:
        if acc[s]:
            ts = sorted(acc[s])
            np.save(os.path.join(OUT, f'{s}.npy'), np.array([[t, *acc[s][t]] for t in ts]))
    months = sorted({time.strftime('%Y-%m', time.gmtime(d / 1000)) for d in days})
    fj = [(s, m) for s in SYMS if not os.path.exists(os.path.join(OUT, f'{s}.fund.npy')) for m in months]
    facc = {}
    with ThreadPoolExecutor(32) as ex:
        for sym, rows in ex.map(funding_month, fj):
            facc.setdefault(sym, []).extend(rows)
    for s, rows in facc.items():
        if rows:
            np.save(os.path.join(OUT, f'{s}.fund.npy'), np.array(sorted(rows), dtype=float))
    print(f'done in {time.time() - t0:.0f}s: ' + ', '.join(
        f'{s} {len(acc.get(s, {}))}h' for s in todo[:6]) + ' ...', flush=True)
