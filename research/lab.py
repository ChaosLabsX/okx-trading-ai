"""
Strategy lab: long-history backtests on hourly bars (2024-01 onward, ~77 coins incl. the
ones the bot has since dropped), for comparing ENTRY rules on equal terms.

Why a second engine beside replay.py: replay.py steps the live rule minute by minute over
13 months, which is the right tool for exits and for checking the live bot. Finding out
whether a different kind of entry has an edge needs more market regimes than 13 months
hold, and hourly bars reach back three times as far for a fraction of the data.

Rules of the house, so results stay honest:
  - A signal is computed on bar i's CLOSE and filled at bar i+1's OPEN (plus slippage).
  - Higher-timeframe values (4H, 1D) come only from bars that had closed by then.
  - Costs: FEE per side (OKX taker 0.10%) + SLIP per side (0.05%) = 0.30% per round trip.
  - Inside one hourly bar the stop is assumed to be hit before the target.
  - Exits are the live "Option 3" shape: stop on everything, sell half at the target, trail
    the rest, stop to break-even once most of the way to the target.
Finalists are re-run on minute data (lab_1m.py) before anything is believed.
"""
import glob, os, sys, time
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as swv

HERE = os.path.dirname(os.path.abspath(__file__))
H = 3_600_000
FEE, SLIP = 0.001, 0.0005
BE_MARGIN = 0.001
TP_BOUNDS, SL_BOUNDS, TRAIL_BOUNDS = (1.5, 10.0), (2.0, 12.0), (1.0, 5.0)   # as signal_checker.py

fmt = lambda ms: time.strftime('%Y-%m-%d %H:%M', time.gmtime(ms / 1000))


# ── data ──────────────────────────────────────────────────────────────────────
class Market:
    """All coins on one hourly grid. Arrays are (coins, bars); NaN before a coin lists."""

    def __init__(self, folder='bars_1h', only=None):
        files = sorted(glob.glob(os.path.join(HERE, folder, '*.npy')))
        raw = {os.path.basename(f)[:-4]: np.load(f) for f in files}
        if only:
            raw = {k: v for k, v in raw.items() if k in only}
        self.syms = sorted(raw)
        self.t0 = int(min(a[0, 0] for a in raw.values()))
        t1 = int(max(a[-1, 0] for a in raw.values()))
        self.n = n = (t1 - self.t0) // H + 1
        self.n -= self.n % 24                       # whole days, so 4H / 1D resampling is exact
        n = self.n
        self.t = self.t0 + np.arange(n) * H
        C = len(self.syms)
        self.o, self.h, self.l, self.c, self.v = (np.full((C, n), np.nan) for _ in range(5))
        for k, s in enumerate(self.syms):
            a = raw[s]
            idx = ((a[:, 0] - self.t0) // H).astype(int)
            ok = (idx >= 0) & (idx < n)
            idx, a = idx[ok], a[ok]
            self.o[k, idx], self.h[k, idx], self.l[k, idx], self.c[k, idx], self.v[k, idx] = a[:, 1:6].T
            first = idx[0]
            last = np.nan                           # forward-fill holes after listing: flat bar, no volume
            for i in range(first, n):
                if np.isnan(self.c[k, i]):
                    if not np.isnan(last):
                        self.o[k, i] = self.h[k, i] = self.l[k, i] = self.c[k, i] = last
                        self.v[k, i] = 0.0
                else:
                    last = self.c[k, i]
        self.ix = {s: k for k, s in enumerate(self.syms)}
        self._cache = {}

    # -- resampling: value of the last higher-timeframe bar that had CLOSED by the close of hourly bar i
    def htf(self, mult):
        key = ('htf', mult)
        if key not in self._cache:
            C, n = self.c.shape
            m = n // mult
            sh = (C, m, mult)
            o = self.o[:, :m * mult].reshape(sh)[:, :, 0]
            h = np.nanmax(np.where(np.isnan(self.h[:, :m * mult]), -np.inf, self.h[:, :m * mult]).reshape(sh), axis=2)
            l = np.nanmin(np.where(np.isnan(self.l[:, :m * mult]), np.inf, self.l[:, :m * mult]).reshape(sh), axis=2)
            c = self.c[:, :m * mult].reshape(sh)[:, :, -1]
            v = np.nansum(self.v[:, :m * mult].reshape(sh), axis=2)
            h[~np.isfinite(h)] = np.nan; l[~np.isfinite(l)] = np.nan
            h[np.isnan(c)] = np.nan; l[np.isnan(c)] = np.nan
            self._cache[key] = (o, h, l, c, v)
        return self._cache[key]

    def to_1h(self, x_htf, mult):
        """Spread a higher-timeframe series onto the hourly grid without look-ahead."""
        n = self.n
        k = (np.arange(n) + 1) // mult - 1
        out = np.full((x_htf.shape[0], n), np.nan)
        ok = k >= 0
        out[:, ok] = x_htf[:, k[ok]]
        return out


# ── indicators (2-D, along time) ──────────────────────────────────────────────
def ema(x, period, alpha=None):
    a = alpha if alpha is not None else 2 / (period + 1)
    out = np.empty_like(x)
    prev = x[:, 0].copy()
    out[:, 0] = prev
    for i in range(1, x.shape[1]):
        xi = x[:, i]
        nxt = np.where(np.isnan(prev), xi, np.where(np.isnan(xi), prev, xi * a + prev * (1 - a)))
        out[:, i] = nxt
        prev = nxt
    return out


def rsi(c, period=14):
    d = np.diff(c, axis=1, prepend=np.nan)
    g, l = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    g[np.isnan(d)] = np.nan; l[np.isnan(d)] = np.nan
    ag, al = ema(g, period, 1 / period), ema(l, period, 1 / period)
    with np.errstate(divide='ignore', invalid='ignore'):
        out = 100 - 100 / (1 + ag / al)
    out[al == 0] = 100.0
    out[np.isnan(ag)] = np.nan
    return out


def atr_pct(h, l, c, period=14):
    pc = np.roll(c, 1, axis=1); pc[:, 0] = np.nan
    tr = np.nanmax(np.stack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    tr[np.isnan(pc)] = np.nan
    return ema(tr, period, 1 / period) / c * 100


def roll(x, w, fn):
    """fn over the PREVIOUS w bars (excludes the current bar). NaN until w bars exist."""
    out = np.full_like(x, np.nan)
    out[:, w:] = fn(swv(x, w, axis=1), axis=2)[:, :-1]
    return out


def roll_incl(x, w, fn):
    """fn over the last w bars INCLUDING the current one."""
    out = np.full_like(x, np.nan)
    out[:, w - 1:] = fn(swv(x, w, axis=1), axis=2)
    return out


def macd(c):
    line = ema(c, 12) - ema(c, 26)
    sig = ema(line, 9)
    prev_l, prev_s = np.roll(line, 1, axis=1), np.roll(sig, 1, axis=1)
    up = (prev_l < prev_s) & (line >= sig)
    dn = (prev_l > prev_s) & (line <= sig)
    return line, sig, up, dn


def pct_b(c, period=20):
    m, s = roll_incl(c, period, np.mean), roll_incl(c, period, np.std)
    with np.errstate(divide='ignore', invalid='ignore'):
        return (c - (m - 2 * s)) / (4 * s)


# ── exits: the live Option 3 shape on hourly bars ─────────────────────────────
def sim_trade(o, h, l, c, ie, tp_pct, sl_pct, trail_pct, tp_frac=0.5, be_frac=0.7, max_bars=None):
    """One trade entered at the open of bar `ie`. Returns (exit bar, kind, net fraction of stake)."""
    n = len(c)
    if ie >= n or np.isnan(o[ie]):
        return None
    e = o[ie] * (1 + SLIP)

    def nf(px):
        px *= (1 - SLIP)
        return (px - e) / e - FEE * (1 + px / e)

    sl_px, tp_px = e * (1 - sl_pct / 100), e * (1 + tp_pct / 100)
    be_trig = e * (1 + be_frac * tp_pct / 100) if be_frac else None
    be_px = e * (1 + FEE) / (1 - FEE) * (1 + BE_MARGIN)
    phase, at_be, banked, peak = 1, False, 0.0, 0.0
    for i in range(ie, n):
        if phase == 1:
            stop = be_px if at_be else sl_px
            if l[i] <= stop:
                return i, ('be' if at_be else 'sl'), nf(min(o[i], stop) if i > ie else stop)
            if h[i] >= tp_px:
                fill = max(o[i], tp_px) if i > ie else tp_px
                if tp_frac >= 1.0:
                    return i, 'tp', nf(fill)
                phase, banked, peak = 2, nf(fill) * tp_frac, fill
                continue
            if be_trig and not at_be and h[i] >= be_trig:
                at_be = True
            if max_bars and i - ie >= max_bars:
                return i, 'time', nf(c[i])
            continue
        floor = peak * (1 - trail_pct / 100)
        if l[i] <= floor:
            return i, 'tp_trail', banked + nf(min(o[i], floor)) * (1 - tp_frac)
        peak = max(peak, h[i])
    last = c[n - 1]
    return n - 1, 'open', (nf(last) if phase == 1 else banked + nf(last) * (1 - tp_frac))


def exit_pcts(atr, tp_m, sl_m, tr_m):
    tp = min(max(tp_m * atr, TP_BOUNDS[0]), TP_BOUNDS[1])
    sl = min(max(sl_m * atr, SL_BOUNDS[0]), SL_BOUNDS[1])
    tr = max(TRAIL_BOUNDS[0], min(tr_m * atr, TRAIL_BOUNDS[1], tp - 0.5))
    return tp, sl, tr


# ── running a rule ────────────────────────────────────────────────────────────
def run(mk, sig, atrp, X, rank=None, i_from=0, i_to=None, cooldown=4, max_open=3, per_bar=1,
        portfolio=True):
    """
    sig: bool (coins, bars). X: dict(tp, sl, tr [ATR multiples], tp_frac, be_frac, max_bars).
    Returns (setups, book): `setups` = every signal traded alone (one per coin at a time, with
    the per-coin cooldown); `book` = the same under the live portfolio limits (max_open
    positions, per_bar new trades per hour, best `rank` first).
    """
    i_to = mk.n - 1 if i_to is None else i_to
    C = len(mk.syms)
    setups = []
    by_bar = {}
    for k in range(C):
        idx = np.flatnonzero(sig[k, i_from:i_to]) + i_from
        busy_until, last_sig = -1, -10**9
        o, h, l, c = mk.o[k], mk.h[k], mk.l[k], mk.c[k]
        for i in idx:
            if i - last_sig < cooldown:
                continue
            last_sig = i
            a = atrp[k, i]
            if np.isnan(a) or a <= 0 or i + 1 >= mk.n:
                continue
            tp, sl, tr = exit_pcts(a, X['tp'], X['sl'], X['tr'])
            out = sim_trade(o, h, l, c, i + 1, tp, sl, tr, X.get('tp_frac', 0.5), X.get('be_frac', 0.7),
                            X.get('max_bars'))
            if out is None:
                continue
            tr_ = {'k': k, 'i': i, 'x': out[0], 'kind': out[1], 'r': out[2],
                   'rank': float(rank[k, i]) if rank is not None else 0.0}
            by_bar.setdefault(i, []).append(tr_)
            if i > busy_until:
                setups.append(tr_)
                busy_until = out[0]
    book = []
    if portfolio:
        open_until = {}                             # coin -> exit bar
        for i in sorted(by_bar):
            for k in [k for k, x in open_until.items() if x <= i]:
                del open_until[k]
            cands = [t for t in by_bar[i] if t['k'] not in open_until]
            cands.sort(key=lambda t: t['rank'], reverse=True)
            for t in cands[:max(0, min(per_bar, max_open - len(open_until)))]:
                open_until[t['k']] = t['x']
                book.append(t)
    return setups, book


def stats(mk, trades):
    if not trades:
        return {'n': 0}
    r = np.array([t['r'] for t in trades]) * 100
    order = np.argsort([t['i'] for t in trades])
    cum = np.cumsum(r[order])
    dd = float(np.max(np.maximum.accumulate(np.maximum(cum, 0)) - cum)) if len(cum) else 0.0
    win, loss = r[r > 0].sum(), -r[r <= 0].sum()
    se = r.std(ddof=1) / np.sqrt(len(r)) if len(r) > 1 else float('nan')
    yrs = {}
    for t, x in zip(trades, r):
        y = time.gmtime(mk.t[t['i']] / 1000).tm_year
        yrs.setdefault(y, []).append(x)
    hold = np.mean([t['x'] - t['i'] for t in trades])
    return {'n': len(r), 'wr': float((r > 0).mean()), 'avg': float(r.mean()), 'med': float(np.median(r)),
            'net': float(r.sum()), 'pf': float(win / loss) if loss else float('inf'), 'dd': dd,
            't': float(r.mean() / se) if se and se > 0 else float('nan'), 'hold': float(hold),
            'yr': {y: (len(v), float(np.mean(v))) for y, v in sorted(yrs.items())}}


def line(name, s):
    if not s.get('n'):
        return f'{name:<34} no trades'
    yr = '  '.join(f"{y % 100}:{n:>4}/{a:+.2f}" for y, (n, a) in s['yr'].items())
    return (f"{name:<34} n {s['n']:>5}  win {s['wr']:>4.0%}  avg {s['avg']:+.3f}%  t {s['t']:+.1f}  "
            f"PF {s['pf']:.2f}  net {s['net']:+7.0f}  dd {s['dd']:>5.0f}  hold {s['hold']:>4.0f}h | {yr}")
