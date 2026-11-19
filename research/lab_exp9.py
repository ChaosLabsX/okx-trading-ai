"""Lab experiment 9: seven ideas from outside the bot, each tested on two separate periods.

The owner asked (2026-11-19) for new ideas that might work, tested before anything changes.
Sources: published studies where they exist, otherwise ideas traders widely use.

  1 time of day / day of week   Quantpedia: BTC's best hours are 21-23 UTC; weekday effects
  2 funding carry               buy the coin + short its perpetual: earn funding, no price bet
  3 weekly losers               literature: last week's worst coins bounce (short-term reversal)
  4 Fear & Greed                buy BTC when the alternative.me index shows extreme fear
  5 volatility breakout         Larry Williams: buy when price clears open + k x yesterday's range
  6 short squeeze               price up hard while open interest falls (mirror of crash rebound)
  7 BTC leads, alts follow      after a sharp BTC rise, buy the alts that have not moved yet

House rules (as lab.py): decide on a closed bar, fill at the next open, 0.10% fee + 0.05%
slippage per side (0.30% round trip on spot). Statistics count each calendar DAY once, because
signals cluster (a market-wide move fires many coins at the same hour); `t` below is that
day-clustered t-statistic: about 2 or more means unlikely to be luck.

Periods: 2024-01 -> 2026-11 and, separately, 2022-2023 (or 2018-2023 for daily majors).
An idea is only worth money if it holds in BOTH.

usage: lab_exp9.py [1..7 | all]
"""
import json, os, sys, time
import numpy as np
import requests

import lab
from lab_d1 import Daily

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
H, D = 3_600_000, 86_400_000
COST = 2 * (lab.FEE + lab.SLIP)            # spot round trip, 0.30%
day_of = lambda ms: int(ms // D)
ymd = lambda ms: time.strftime('%Y-%m-%d', time.gmtime(ms / 1000))


# ── shared statistics ─────────────────────────────────────────────────────────
def st(rets, days):
    """Per-trade net returns (fractions) and each trade's entry day -> summary dict."""
    r = np.asarray(rets, float)
    if len(r) == 0:
        return {'n': 0}
    by = {}
    for x, d in zip(r, days):
        by.setdefault(d, []).append(x)
    dm = np.array([np.mean(v) for v in by.values()])
    se = dm.std(ddof=1) / np.sqrt(len(dm)) if len(dm) > 2 else np.nan
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    return {'n': len(r), 'days': len(dm), 'avg': r.mean() * 100, 'med': np.median(r) * 100,
            'win': (r > 0).mean() * 100, 'pf': gains / losses if losses > 0 else 99.0,
            't': dm.mean() / se if se and se > 0 else np.nan, 'worst': r.min() * 100}


def show(name, s, width=46):
    if not s or s['n'] == 0:
        return f'{name:<{width}} no trades'
    return (f"{name:<{width}} n {s['n']:>5} days {s['days']:>4}  avg {s['avg']:>+6.2f}%  med {s['med']:>+6.2f}%  "
            f"win {s['win']:>3.0f}%  PF {s['pf']:>5.2f}  t {s['t']:>+5.1f}  worst {s['worst']:>+6.1f}%")


def curve(rets):
    """Daily (or weekly) portfolio returns -> total, CAGR-ready equity, max drawdown."""
    eq = np.cumprod(1 + np.asarray(rets, float))
    dd = float(np.max(1 - eq / np.maximum.accumulate(eq))) if len(eq) else 0.0
    return eq, dd


def by_year(rets, years):
    rets, years = np.asarray(rets, float), np.asarray(years)
    return ' '.join(f"{y % 100}:{np.prod(1 + rets[years == y]) - 1:+.0%}" for y in sorted(set(years.tolist())))


# ── 1. time of day / day of week ──────────────────────────────────────────────
def idea1():
    print('=== 1. TIME OF DAY / DAY OF WEEK ===')
    print('Claim (Quantpedia, 2015-2022 data): BTC earns most between 21:00 and 24:00 UTC.\n')
    sets = {}
    for folder, label in (('bars_1h_oos', '2022-23'), ('bars_1h', '2024-26')):
        mk = lab.Market(folder, only={'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'BNBUSDT'})
        sets[label] = mk
    print('average return of each UTC hour, basis points (1 bp = 0.01%):')
    print('hour  ' + '  '.join(f'{s:>15}' for s in ('BTC 2022-23', 'BTC 2024-26', 'ETH 2022-23', 'ETH 2024-26')))
    hr_avg = {}
    for sym in ('BTCUSDT', 'ETHUSDT'):
        for label, mk in sets.items():
            k = mk.ix[sym]
            r = mk.c[k] / mk.o[k] - 1
            hrs = (mk.t // H) % 24
            hr_avg[(sym, label)] = np.array([np.nanmean(r[hrs == h_]) * 1e4 for h_ in range(24)])
    for h_ in range(24):
        print(f'{h_:02d}    ' + '  '.join(f"{hr_avg[(s, l)][h_]:>+15.1f}" for s in ('BTCUSDT', 'ETHUSDT') for l in ('2022-23', '2024-26')))
    corr = np.corrcoef(hr_avg[('BTCUSDT', '2022-23')], hr_avg[('BTCUSDT', '2024-26')])[0, 1]
    print(f'BTC: does the hour pattern of 2022-23 repeat in 2024-26? correlation {corr:+.2f} (1 = same, 0 = unrelated)\n')

    # strategies: hold only during a window each day (buy at its first hour's open, sell at its last close)
    best3 = max(range(24), key=lambda s0: sum(hr_avg[('BTCUSDT', '2022-23')][(s0 + j) % 24] for j in range(3)))
    windows = [('21:00-24:00 (published)', 21, 3), (f'{best3:02d}:00 +3h (best of 2022-23)', best3, 3)]
    print('hold BTC / 5 majors only inside a window, every day; per-day result, then a year of it:')
    for wname, s0, L in windows:
        for label, mk in sets.items():
            hrs = (mk.t // H) % 24
            starts = np.where(hrs == s0)[0]
            starts = starts[starts + L <= mk.n]
            for who in ('BTC', '5 majors'):
                ks = [mk.ix['BTCUSDT']] if who == 'BTC' else list(range(len(mk.syms)))
                g = np.nanmean([[mk.c[k, i + L - 1] / mk.o[k, i] - 1 for i in starts] for k in ks], axis=0)
                gross = np.nanmean(g)
                print(f'  {wname:<28} {label} {who:<9} gross {gross * 1e4:>+6.1f} bp/day = {gross * 365:>+6.0%}/yr'
                      f'   after 0.30% costs {(gross - COST) * 365:>+6.0%}/yr   after 0.16% (limit orders) {(gross - 0.0016) * 365:>+6.0%}/yr')
    # day of week, BTC daily 2018-2026
    a = np.load(os.path.join(HERE, 'bars_maj_1d', 'BTCUSDT.npy'))
    r = a[:, 4] / a[:, 1] - 1
    wd = np.array([time.gmtime(t / 1000).tm_wday for t in a[:, 0]])
    yr = np.array([time.gmtime(t / 1000).tm_year for t in a[:, 0]])
    names = 'Mon Tue Wed Thu Fri Sat Sun'.split()
    print('\nBTC average return by weekday (UTC), %:')
    for lo, hi in ((2018, 2022), (2023, 2026)):
        m = (yr >= lo) & (yr <= hi)
        print(f'  {lo}-{hi}: ' + '  '.join(f'{names[w]} {np.mean(r[m & (wd == w)]) * 100:+.2f}' for w in range(7)))
    m1 = (yr <= 2022)
    bw = max(range(7), key=lambda w: np.mean(r[m1 & (wd == w)]))
    m2 = (yr >= 2023) & (wd == bw)
    print(f'  best weekday in 2018-22 was {names[bw]}; holding only on {names[bw]} in 2023-26: '
          f'{np.mean(r[m2]) * 100:+.2f}%/week before costs, {(np.mean(r[m2]) - COST) * 100:+.2f}% after, '
          f'{np.prod(1 + r[m2] - COST) - 1:+.0%} in total')
    print()


# ── 2. funding carry ──────────────────────────────────────────────────────────
def fund_daily(folder):
    """{coin: (first_day, array of daily funding sums)} from Binance funding prints."""
    out = {}
    for f in sorted(os.listdir(os.path.join(HERE, folder))):
        if not f.endswith('.fund.npy'):
            continue
        a = np.load(os.path.join(HERE, folder, f))
        d = (a[:, 0] // D).astype(int)
        d0 = d.min()
        s = np.full(d.max() - d0 + 1, np.nan)
        for di, v in zip(d - d0, a[:, 1]):
            s[di] = (0 if np.isnan(s[di]) else s[di]) + v
        out[f.split('.')[0]] = (d0, s)
    return out


def idea2():
    print('=== 2. FUNDING CARRY: own the coin AND short its perpetual (no bet on price) ===')
    print('Earns the funding that perpetual longs pay shorts. Costs: 4 trades per round trip '
          '(spot 0.10% + perp 0.05% fee, 0.05% slippage each) = 0.50% of the position.\n')
    RT = 2 * (0.0010 + 0.0005 + 0.0005 + 0.0005)       # spot fee+slip, perp fee+slip, both ways
    for folder, label in (('derivs_oos', '2022-23'), ('derivs', '2024-26')):
        F = fund_daily(folder)
        print(f'-- {label}: {len(F)} coins')
        allon = []
        for coin, (d0, s) in F.items():
            ok = ~np.isnan(s)
            allon.append(np.nansum(s) / ok.sum() * 365)
        print(f'   simply holding the hedge all the time: funding earned {np.mean(allon):+.1%}/yr on average '
              f'(BTC {np.nansum(F["BTC"][1]) / (~np.isnan(F["BTC"][1])).sum() * 365:+.1%}, '
              f'ETH {np.nansum(F["ETH"][1]) / (~np.isnan(F["ETH"][1])).sum() * 365:+.1%}); worst coin {min(allon):+.1%}')
        for enter, leave in ((0.10, 0.03), (0.20, 0.05), (0.30, 0.10)):
            per_coin, yrs_all, rets_all = [], [], {}
            for coin, (d0, s) in F.items():
                s0 = np.nan_to_num(s)
                on, pnl, days_in = False, np.zeros(len(s)), 0
                for i in range(3, len(s)):
                    trail = s0[i - 3:i].mean() * 365       # funding of the last 3 FULL days, annualised
                    if not on and trail > enter:
                        on = True; pnl[i] -= RT / 2
                    elif on and trail < leave:
                        on = False; pnl[i] -= RT / 2
                    if on:
                        pnl[i] += s0[i]; days_in += 1
                per_coin.append((coin, pnl.sum() / (len(s) / 365), days_in / len(s)))
                for i, x in enumerate(pnl):
                    rets_all.setdefault(d0 + i, []).append(x)
            days = sorted(rets_all)
            port = np.array([np.mean(rets_all[d]) for d in days])    # each coin a fixed equal slice of capital
            yrs = np.array([time.gmtime(d * 86400).tm_year for d in days])
            eq, dd = curve(port)
            cagr = eq[-1] ** (365 / len(port)) - 1
            best = sorted(per_coin, key=lambda x: -x[1])
            print(f'   in when last 3 days pay > {enter:.0%}/yr, out under {leave:.0%}/yr: portfolio {cagr:+.1%}/yr, '
                  f'max dd {dd:.1%}, time in {np.mean([p[2] for p in per_coin]):.0%} | {by_year(port, yrs)}'
                  f' | best {best[0][0]} {best[0][1]:+.0%}/yr, worst {best[-1][0]} {best[-1][1]:+.0%}/yr')
    print('   (per $ of coin held; the short needs margin on top, so per $ of capital it is roughly 2/3 to 1/2 of this)\n')


# ── 3. weekly losers ──────────────────────────────────────────────────────────
def idea3():
    print('=== 3. WEEKLY LOSERS: buy last week\'s worst coins, hold a week ===')
    print('Literature: short-term reversal, strongest in past losers. Universe = every Binance USDT pair '
          '(delisted ones included), only the N most traded of the previous 30 days.\n')
    mk = Daily('bars_all_1d')
    C, n = mk.c.shape
    c, o = mk.c, mk.o
    vol30 = lab.roll(mk.v, 30, np.mean)
    age = np.cumsum(~np.isnan(c), axis=1) >= 60
    stables = {k for s, k in mk.ix.items() if s[:-4] in ('USDC', 'FDUSD', 'TUSD', 'BUSD', 'DAI', 'USDP', 'EUR', 'AEUR', 'USDE', 'XUSD', 'USD1', 'PAXG', 'WBTC', 'WBETH', 'BFUSD', 'RLUSD')}

    def exit_px(k, d):
        seg = c[k, :d + 1]
        if not np.isnan(o[k, d]):
            return o[k, d]
        v = seg[~np.isnan(seg)]
        return v[-1] if len(v) else np.nan

    for N in (50, 20):
        vr = np.where(age & ~np.isnan(c), vol30, -1.0); vr[np.isnan(vr)] = -1.0
        for k in stables:
            vr[k] = -1.0
        order = np.argsort(-vr, axis=0)
        rk = np.empty_like(order); np.put_along_axis(rk, order, np.arange(C)[:, None], axis=0)
        uni = (rk < N) & (vr > 0)
        for HOLD, LOOK in ((7, 7), (1, 1), (3, 3)):
            rows = {'losers 5': [], 'winners 5': [], 'all (benchmark)': []}
            dates = []
            for d in range(120, n - HOLD - 1, HOLD):
                m = uni[:, d] & ~np.isnan(c[:, d - LOOK]) & ~np.isnan(o[:, d + 1])
                ks = np.where(m)[0]
                if len(ks) < 10:
                    continue
                r_past = c[ks, d] / c[ks, d - LOOK] - 1
                srt = ks[np.argsort(r_past)]
                fwd = lambda kk: np.mean([exit_px(k, d + 1 + HOLD) / o[k, d + 1] - 1 - COST for k in kk])
                rows['losers 5'].append(fwd(srt[:5])); rows['winners 5'].append(fwd(srt[-5:]))
                rows['all (benchmark)'].append(np.mean([exit_px(k, d + 1 + HOLD) / o[k, d + 1] - 1 for k in ks]))
                dates.append(mk.year[d])
            print(f'-- top {N} coins, look back {LOOK}d, hold {HOLD}d ({len(dates)} rebalances, 2023-05 -> 2026-11):')
            for name, rr in rows.items():
                rr = np.array(rr)
                eq, dd = curve(rr)
                per = 365 / HOLD
                cagr = eq[-1] ** (per / len(rr)) - 1
                t = rr.mean() / (rr.std(ddof=1) / np.sqrt(len(rr)))
                print(f'   {name:<16} avg {rr.mean() * 100:>+6.2f}% per hold  t {t:>+5.1f}  CAGR {cagr:>+6.0%}  max dd {dd:>4.0%} | {by_year(rr, dates)}')
    print('   (losers/winners pay 0.30% per round trip; the benchmark is shown before costs)\n')


# ── 4. Fear & Greed ───────────────────────────────────────────────────────────
def idea4():
    print('=== 4. FEAR & GREED: buy BTC on extreme fear (alternative.me index, daily since 2018-02) ===')
    fp = os.path.join(HERE, 'fng.json')
    if not os.path.exists(fp):
        r = requests.get('https://api.alternative.me/fng/', params={'limit': 0, 'format': 'json'}, timeout=30)
        r.raise_for_status()
        json.dump(r.json()['data'], open(fp, 'w'))
    fng = {int(x['timestamp']) // 86400: int(x['value']) for x in json.load(open(fp))}
    a = np.load(os.path.join(HERE, 'bars_maj_1d', 'BTCUSDT.npy'))
    dd_ = (a[:, 0] // D).astype(int)
    o, c = a[:, 1], a[:, 4]
    yr = np.array([time.gmtime(t / 1000).tm_year for t in a[:, 0]])
    val = np.array([fng.get(d, np.nan) for d in dd_])
    print(f'index known for {np.mean(~np.isnan(val[dd_ >= min(fng)])):.0%} of days since {ymd(min(fng) * D)}; '
          f'days at or under 10: {(val <= 10).sum()}, under 20: {(val <= 20).sum()}\n')
    for lo, hi in ((2018, 2022), (2023, 2026)):
        print(f'-- {lo}-{hi}')
        per = (yr >= lo) & (yr <= hi)
        for HOLD in (30, 90):
            base = [o[i + 1 + HOLD] / o[i + 1] - 1 for i in np.where(per)[0] if i + 1 + HOLD < len(o) and not np.isnan(val[i])]
            print(f'   any day, hold {HOLD}d (the yardstick): avg {np.mean(base) * 100:+.1f}%  up {np.mean(np.array(base) > 0):.0%} of the time')
            for X in (10, 15, 20, 25):
                rets, ds, i = [], [], 0
                idx = np.where(per)[0]
                busy_until = -1
                for i in idx:
                    if i <= busy_until or i + 1 + HOLD >= len(o) or np.isnan(val[i]) or val[i] > X:
                        continue
                    rets.append(o[i + 1 + HOLD] / o[i + 1] - 1 - COST); ds.append(i); busy_until = i + HOLD
                if rets:
                    rr = np.array(rets)
                    print(f'   fear <= {X:>2}, hold {HOLD}d: {len(rr):>2} separate trades  avg {rr.mean() * 100:>+6.1f}%  '
                          f'win {np.mean(rr > 0):.0%}  worst {rr.min() * 100:+.0f}%  best {rr.max() * 100:+.0f}%')
    print()


# ── 5. volatility breakout ────────────────────────────────────────────────────
def idea5():
    print('=== 5. VOLATILITY BREAKOUT (Larry Williams): buy when price clears open + k x yesterday\'s range, '
          'sell at the next day\'s open ===')
    print('12 majors (largest in Jan 2023, so no hindsight), daily bars 2018 -> 2026.\n')
    MAJ = 'BTC ETH BNB XRP ADA DOGE SOL LTC TRX DOT AVAX LINK'.split()
    data = {m: np.load(os.path.join(HERE, 'bars_maj_1d', f'{m}USDT.npy')) for m in MAJ}
    for k_ in (0.3, 0.5, 0.7):
        for filt in ('none', 'open above 5-day average', 'coin above 50-day EMA'):
            trades, tdays, port = [], [], {}
            for m, a in data.items():
                t, o, h, l, c = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]
                e50 = lab.ema(c[None, :], 50)[0]
                for i in range(60, len(a) - 1):
                    if filt == 'open above 5-day average' and not o[i] > c[i - 5:i].mean():
                        continue
                    if filt == 'coin above 50-day EMA' and not c[i - 1] > e50[i - 1]:
                        continue
                    trig = o[i] + k_ * (h[i - 1] - l[i - 1])
                    if h[i] < trig:
                        port.setdefault(int(t[i] // D), []).append((m, 0.0)); continue
                    entry = max(o[i], trig) * (1 + lab.SLIP)
                    r = o[i + 1] * (1 - lab.SLIP) / entry - 1 - 2 * lab.FEE
                    trades.append(r); tdays.append(int(t[i] // D))
                    port.setdefault(int(t[i] // D), []).append((m, r))
            days = sorted(port)
            pr = np.array([np.mean([x for _, x in port[d]]) for d in days])
            yrs = np.array([time.gmtime(d * 86400).tm_year for d in days])
            tr, td = np.array(trades), np.array(tdays)
            tyr = np.array([time.gmtime(d * 86400).tm_year for d in td])
            a_ = st(tr[tyr <= 2023], td[tyr <= 2023]); b_ = st(tr[tyr >= 2024], td[tyr >= 2024])
            print(f'k {k_}  filter: {filt}')
            print('   ' + show('2018-2023', a_, 12)); print('   ' + show('2024-2026', b_, 12))
            print(f'   12-coin portfolio by year: {by_year(pr, yrs)}')
    # benchmark
    port = {}
    for m, a in data.items():
        for i in range(61, len(a)):
            port.setdefault(int(a[i, 0] // D), []).append(a[i, 4] / a[i - 1, 4] - 1)
    days = sorted(port)
    pr = np.array([np.mean(port[d]) for d in days]); yrs = np.array([time.gmtime(d * 86400).tm_year for d in days])
    print(f'benchmark, simply holding all 12: {by_year(pr, yrs)}\n')


# ── hourly + futures data, shared by 6 and 7 ──────────────────────────────────
def load_hourly(bars, der):
    syms = sorted(f[:-4] for f in os.listdir(os.path.join(HERE, der)) if f.endswith('.npy') and '.fund' not in f)
    mk = lab.Market(bars, only={s + 'USDT' for s in syms})
    C, n = mk.c.shape
    oi = np.full((C, n), np.nan)
    for s in syms:
        if s + 'USDT' not in mk.ix:
            continue
        k = mk.ix[s + 'USDT']
        a = np.load(os.path.join(HERE, der, f'{s}.npy'))
        idx = ((a[:, 0] - mk.t0) // H).astype(int)
        okk = (idx >= 0) & (idx < n)
        oi[k, idx[okk]] = a[okk, 1]
        last = np.nan
        for i in range(n):
            if np.isnan(oi[k, i]): oi[k, i] = last
            else: last = oi[k, i]
    return mk, oi / mk.c          # open interest in coins, not dollars (dollars fall with price)


def events(mk, sig, hold, cooldown=24):
    """One trade per coin per `cooldown` hours: buy next open, sell `hold` hours later at the open."""
    C, n = mk.c.shape
    rets, days = [], []
    for k in range(C):
        nxt = -1
        for i in np.where(sig[k])[0]:
            if i < nxt or i + 1 + hold >= n:
                continue
            rets.append(mk.o[k, i + 1 + hold] / mk.o[k, i + 1] - 1 - COST)
            days.append(int(mk.t[i] // D)); nxt = i + cooldown
    return rets, days


def idea6():
    print('=== 6. SHORT SQUEEZE: price up hard in 24h while open interest FALLS (shorts forced out) ===')
    print('The mirror of crash rebound. Bought at the next hour\'s open, sold 24h / 48h later.\n')
    for bars, der, label in (('bars_1h_oos', 'derivs_oos', '2022-23'), ('bars_1h', 'derivs', '2024-26')):
        mk, oi = load_hourly(bars, der)
        C, n = mk.c.shape
        with np.errstate(all='ignore'):
            r24 = np.full((C, n), np.nan); r24[:, 24:] = mk.c[:, 24:] / mk.c[:, :-24] - 1
            o24 = np.full((C, n), np.nan); o24[:, 24:] = oi[:, 24:] / oi[:, :-24] - 1
        print(f'-- {label}: {C} coins')
        for hold in (24, 48):
            for name, sig in ((f'up >= 8% (any)', r24 >= 0.08),
                              (f'up >= 8%, OI down >= 10% (squeeze)', (r24 >= 0.08) & (o24 <= -0.10)),
                              (f'up >= 8%, OI down >= 20% (hard squeeze)', (r24 >= 0.08) & (o24 <= -0.20)),
                              (f'up >= 8%, OI up >= 15% (new longs pile in)', (r24 >= 0.08) & (o24 >= 0.15)),
                              (f'up >= 15%, OI down >= 10%', (r24 >= 0.15) & (o24 <= -0.10))):
                r, d = events(mk, np.nan_to_num(sig).astype(bool), hold)
                print('   ' + show(f'{name}, hold {hold}h', st(r, d), 50))
    print()


def idea7():
    print('=== 7. BTC LEADS, ALTS FOLLOW: after BTC jumps, buy the 5 alts that moved least ===')
    for bars, label in (('bars_1h_oos', '2022-23'), ('bars_1h', '2024-26')):
        mk = lab.Market(bars)
        C, n = mk.c.shape
        b = mk.ix['BTCUSDT']
        alts = [k for s, k in mk.ix.items() if s not in ('BTCUSDT',)]
        print(f'-- {label}: {len(alts)} alts')
        for L, jump in ((4, 0.02), (4, 0.03), (1, 0.015)):
            rb = np.full(n, np.nan); rb[L:] = mk.c[b, L:] / mk.c[b, :-L] - 1
            for hold in (12, 24):
                lag, lead, allr, days, nxt = [], [], [], [], -1
                for i in np.where(rb >= jump)[0]:
                    if i < nxt or i + 1 + hold >= n:
                        continue
                    ks = [k for k in alts if not np.isnan(mk.c[k, i - L]) and mk.v[k, i] > 0 and not np.isnan(mk.o[k, i + 1 + hold])]
                    if len(ks) < 10:
                        continue
                    ra = np.array([mk.c[k, i] / mk.c[k, i - L] - 1 for k in ks])
                    srt = [ks[j] for j in np.argsort(ra)]
                    fwd = lambda kk: np.mean([mk.o[k, i + 1 + hold] / mk.o[k, i + 1] - 1 for k in kk])
                    lag.append(fwd(srt[:5]) - COST); lead.append(fwd(srt[-5:]) - COST); allr.append(fwd(ks) - COST)
                    days.append(int(mk.t[i] // D)); nxt = i + hold
                tag = f'BTC +{jump:.1%} in {L}h, hold {hold}h'
                print('   ' + show(f'{tag}: 5 laggards', st(lag, days), 44))
                print('   ' + show(f'{tag}: 5 leaders', st(lead, days), 44))
                print('   ' + show(f'{tag}: all alts', st(allr, days), 44))
    print()


if __name__ == '__main__':
    which = sys.argv[1] if len(sys.argv) > 1 else 'all'
    for k, fn in (('1', idea1), ('2', idea2), ('3', idea3), ('4', idea4), ('5', idea5), ('6', idea6), ('7', idea7)):
        if which in ('all', k):
            t0 = time.time()
            with np.errstate(all='ignore'):
                fn()
            sys.stdout.flush()
