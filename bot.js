'use strict';
// ═══════════════════════════════════════════════════════════
//  BOT PANEL — status strip, strategies, "why no trade?", system
//
//  Everything here is read-only: three small Supabase reads with the public key.
//    bot_status      one row the worker rewrites every ~5 minutes (alive? market? funnel)
//    paper_trades    simulated trades of the strategies that are not live
//    skipped_setups  every qualified signal that was NOT traded, and who refused it
//  The status row loads with the page; the other two load when their tab is first opened.
//  Uses el / escHtml / sbHeaders / getSupabaseCfg / fmtSigned / state from app.js.
// ═══════════════════════════════════════════════════════════

// The bar a strategy has to clear to move up the ladder (paper -> $20 trial -> full size).
// Written down in docs/STRATEGIES.md BEFORE any result existed, so it cannot drift toward
// whatever the results turn out to be. The worker applies it (strategies.py) and reports
// its numbers in the status row; this copy is only the fallback when that row is missing.
const PROMO = { minTrades: 30, minPF: 1.3, seMult: 1.3, fullMinLive: 10 };

const STRATEGY_INFO = {
  dip:   { name: 'Dip buy', what: 'STRONG BUY signal + 30m reversal + AI advisor. The original strategy.' },
  flush: { name: 'Crash rebound', what: 'Coin down 8%+ in a day while futures open interest collapses 15%+ (forced selling). Buy, hold 48h, protective stop −30%.' },
  trend: { name: 'Trend (majors)', what: 'The 12 largest coins, each held while 2% above its 50-day average.' },
};

const GATE_INFO = {
  regime:      'BTC downtrend filter',
  max_open:    'Open-trade cap',
  breaker:     'Stop-loss circuit breaker',
  loss_limit:  'Monthly loss limit',
  correlation: 'Correlation guard',
  cap:         'Ranked below the top pick',
  funding:     'Crowded funding',
  ai:          'AI advisor',
};

// ── pure helpers (tested in tests/test_dashboard_bot.js) ───────────────────────
function paperStats(rows) {
  const out = {};
  const get = s => (out[s] ||= { n: 0, wins: 0, sum: 0, sq: 0, gw: 0, gl: 0, open: 0, usdt: 0 });
  for (const t of rows || []) {
    const s = get(t.strategy);
    if (!t.closed_at) { s.open += 1; continue; }
    const p = +t.net_pct || 0;
    s.n += 1; s.wins += p > 0 ? 1 : 0; s.sum += p; s.sq += p * p;
    s.gw += Math.max(p, 0); s.gl += Math.max(-p, 0); s.usdt += +t.net_pnl_usdt || 0;
  }
  for (const s of Object.values(out)) {
    s.avg = s.n ? s.sum / s.n : 0;
    s.pf = s.gl > 0 ? s.gw / s.gl : (s.gw > 0 ? Infinity : null);
    const variance = s.n > 1 ? Math.max(0, (s.sq - s.n * s.avg * s.avg) / (s.n - 1)) : 0;
    s.se = s.n > 1 ? Math.sqrt(variance / s.n) : null;
    s.checks = [
      { ok: s.n >= PROMO.minTrades, text: `${PROMO.minTrades}+ closed trades (has ${s.n})` },
      { ok: s.n > 0 && s.avg > 0, text: `average result above zero (${s.n ? fmtPct(s.avg) : 'n/a'})` },
      { ok: s.pf !== null && s.pf >= PROMO.minPF, text: `profit factor ${PROMO.minPF}+ (${s.pf === null ? 'n/a' : s.pf === Infinity ? 'no losses' : s.pf.toFixed(2)})` },
      { ok: s.se !== null && s.avg - PROMO.seMult * s.se > 0, text: 'average clears its own margin of error (not luck)' },
    ];
    const passed = s.checks.every(c => c.ok);
    s.verdict = passed ? 'pass'
      : s.n >= PROMO.minTrades && s.avg <= 0 ? 'fail'
      : 'wait';
  }
  return out;
}

function fmtPct(v, digits = 2) { return `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(digits)}%`; }

// One strategy as the worker's ladder sees it (status row -> ladder.strategies[name]):
// the four checks, a verdict, and what would move it up or down from here.
function ladderView(s, level, bar = {}, stake = null) {
  const minT = bar.min_trades ?? PROMO.minTrades, minPF = bar.min_pf ?? PROMO.minPF;
  const k = bar.se_mult ?? PROMO.seMult, fullLive = bar.full_min_live ?? PROMO.fullMinLive;
  const pf = s.pf === null || s.pf === undefined ? null : (s.pf >= 999 ? Infinity : s.pf);
  const checks = [
    { ok: s.n >= minT, text: `${minT}+ closed trades (has ${s.n}${s.n ? `: ${s.n_live} real, ${s.n_paper} paper` : ''})` },
    { ok: s.n > 0 && s.mean > 0, text: `average result above zero (${s.n ? fmtPct(s.mean) : 'n/a'})` },
    { ok: pf !== null && pf >= minPF, text: `profit factor ${minPF}+ (${pf === null ? 'n/a' : pf === Infinity ? 'no losses' : pf.toFixed(2)})` },
    { ok: s.se !== null && s.se !== undefined && s.day_mean - k * s.se > 0,
      text: `not luck: the daily average clears its margin of error${s.days ? ` (${s.days} trading day${s.days === 1 ? '' : 's'})` : ''}` },
  ];
  const passes = checks.every(c => c.ok);
  const live = level === 'trial' || level === 'full';
  let cls, verdict;
  if (level === 'full') { cls = 'good'; verdict = 'Proven — trading real money at full size'; }
  else if (level === 'trial') { cls = 'good'; verdict = `Trading real money on a trial: $${stake ?? 20} a trade`; }
  else if (level === 'off') { cls = ''; verdict = 'Switched off'; }
  else if (s.n >= minT && s.mean <= 0) { cls = 'bad'; verdict = 'Paper — it lost money on average'; }
  else { cls = 'warn'; verdict = 'Paper — not enough evidence yet'; }
  const next = level === 'paper' ? 'Moves to a $20 real-money trial by itself once all four checks pass.'
    : level === 'trial' ? `Moves to full size once all four checks pass with ${fullLive}+ real trades in its record (has ${s.n_live}). `
      + 'Back to paper if it loses on average over 30 trades, or its real trades lose more than 5% of the account in 30 days.'
    : level === 'full' ? 'Back to paper if it starts losing on average, or loses more than 5% of the account in 30 days.'
    : '';
  return { checks, passes, live, cls, verdict, next };
}

function funnelTotals(funnel, days = 14, todayIso = new Date().toISOString().slice(0, 10)) {
  const keys = Object.keys(funnel || {}).sort().filter(d => d <= todayIso).slice(-days);
  const t = { days: keys.length, list: [] };
  const fields = ['scans', 'bear_scans', 'strong_buy', 'no_reversal', 'low_volume', 'cooldown', 'candidates',
                  'regime', 'max_open', 'breaker', 'loss_limit', 'correlation', 'cap', 'funding', 'ai_skip', 'traded', 'paper'];
  for (const f of fields) t[f] = 0;
  for (const d of keys) {
    const day = funnel[d] || {};
    for (const f of fields) t[f] += +day[f] || 0;
    t.list.push({ date: d, ...day });
  }
  t.bearShare = t.scans ? t.bear_scans / t.scans : null;
  t.taken = t.traded + t.paper;
  t.stopped = ['regime', 'max_open', 'breaker', 'loss_limit', 'correlation', 'cap', 'funding', 'ai_skip']
    .map(k => ({ gate: k === 'ai_skip' ? 'ai' : k, n: t[k] })).filter(x => x.n > 0).sort((a, b) => b.n - a.n);
  return t;
}

// What each "no" turned out to be worth, 24h later: the worker replays the suggested
// target and stop on what price did next (signal_checker._grade_skip).
function gateScore(rows) {
  const out = {};
  for (const r of rows || []) {
    const g = (out[r.gate || 'ai'] ||= { n: 0, graded: 0, good: 0, missed: 0, neutral: 0 });
    g.n += 1;
    const v = r.followup && r.followup.verdict;
    if (!v || v === 'ungradable') continue;
    g.graded += 1;
    if (v === 'good_skip') g.good += 1;
    else if (v === 'missed_win') g.missed += 1;
    else g.neutral += 1;
  }
  return out;
}

function ageText(ms) {
  if (ms == null || !isFinite(ms)) return 'never';
  const m = Math.round(ms / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${m % 60}m ago`;
  return `${Math.floor(h / 24)} days ago`;
}

// The worker rewrites its row every ~5 minutes. Two missed runs is a hiccup, more is down.
function statusHealth(updatedAt, now = Date.now()) {
  if (!updatedAt) return { level: 'unknown', age: null };
  const age = now - new Date(updatedAt).getTime();
  return { level: age < 15 * 60000 ? 'ok' : age < 45 * 60000 ? 'late' : 'down', age };
}

if (typeof module !== 'undefined') {
  module.exports = { paperStats, funnelTotals, gateScore, ageText, statusHealth, fmtPct, ladderView, PROMO };
}

// ── data ──────────────────────────────────────────────────────────────────────
const bot = { status: null, statusAt: 0, paper: null, refused: null, tab: 'live', timer: null, wired: false };

async function sbRead(path) {
  const res = await fetch(`${getSupabaseCfg().url}/rest/v1/${path}`, { headers: sbHeaders() });
  if (!res.ok) throw new Error(`Supabase ${res.status}`);
  return res.json();
}

async function loadBotStatus(force = false) {
  if (!force && bot.status && Date.now() - bot.statusAt < 60000) return bot.status;
  try {
    const rows = await sbRead('bot_status?id=eq.worker&select=updated_at,data');
    bot.status = rows[0] || { updated_at: null, data: {} };
  } catch (e) {
    bot.status = { updated_at: null, data: {}, error: e.message };
  }
  bot.statusAt = Date.now();
  renderBotStrip();
  return bot.status;
}

async function loadPaper(force = false) {
  if (bot.paper && !force) return bot.paper;
  bot.paper = await sbRead('paper_trades?order=opened_at.desc&limit=500' +
    '&select=id,strategy,symbol,kind,opened_at,entry_price,amount_usdt,params,closed_at,exit_price,exit_reason,net_pnl_usdt,net_pct,mae_pct,mfe_pct');
  return bot.paper;
}

async function loadRefused(force = false) {
  if (bot.refused && !force) return bot.refused;
  bot.refused = await sbRead('skipped_setups?order=created_at.desc&limit=300' +
    '&select=id,symbol,price,reason,gate,created_at,followup');
  return bot.refused;
}

// ── status strip ──────────────────────────────────────────────────────────────
function renderBotStrip() {
  const box = el('botStrip');
  if (!box) return;
  const s = bot.status || {};
  const d = s.data || {};
  const h = statusHealth(s.updated_at);
  const chips = [];
  if (s.error) {
    chips.push(`<span class="bot-chip bad">⚠ Bot status unavailable (${escHtml(s.error)})</span>`);
  } else if (h.level === 'unknown') {
    chips.push('<span class="bot-chip warn">Bot has not reported yet — update the VPS to the latest version</span>');
  } else {
    const cls = h.level === 'ok' ? 'good' : h.level === 'late' ? 'warn' : 'bad';
    const txt = h.level === 'ok' ? 'Bot running' : h.level === 'late' ? 'Bot is late' : 'Bot is NOT reporting';
    chips.push(`<span class="bot-chip ${cls}"><span class="dot"></span>${txt} · updated ${ageText(h.age)}</span>`);
  }
  if (d.regime && d.regime.msg) {
    chips.push(`<span class="bot-chip ${d.regime.ok ? 'good' : 'warn'}" title="${escHtml(d.regime.msg)}">` +
      `${d.regime.ok ? 'Market OK for dip buys' : 'BTC downtrend — dip buys blocked'}</span>`);
  }
  // Each strategy's rung on the ladder (worker's status row); older rows only have modes.
  const lad = (d.ladder || {}).strategies || null;
  const rungs = lad ? Object.fromEntries(Object.entries(lad).map(([k, v]) => [k, { level: v.level, stake: v.stake }]))
                    : Object.fromEntries(Object.entries(d.modes || {}).map(([k, m]) => [k, { level: m === 'live' ? 'full' : m }]));
  for (const [k, r] of Object.entries(rungs)) {
    const info = STRATEGY_INFO[k] || { name: k };
    const txt = r.level === 'trial' ? `REAL · trial $${r.stake ?? 20}` : r.level === 'full' ? 'REAL · full size'
      : r.level === 'paper' ? 'paper' : 'off';
    const cls = r.level === 'trial' || r.level === 'full' ? 'live' : r.level;
    chips.push(`<span class="bot-chip mode-${escHtml(cls)}" title="${escHtml(info.what || '')}">` +
      `${escHtml(info.name)}: ${txt}</span>`);
  }
  if (d.test_mode) chips.push('<span class="bot-chip bad">TEST MODE</span>');
  box.innerHTML = chips.join('');

  const note = el('liveModeNote');
  if (note) {
    const live = Object.entries(rungs).filter(([, r]) => r.level === 'trial' || r.level === 'full')
      .map(([k, r]) => `${(STRATEGY_INFO[k] || { name: k }).name} (${r.level === 'trial' ? `trial, $${r.stake ?? 20} a trade` : 'full size'})`);
    note.style.display = Object.keys(rungs).length ? '' : 'none';
    note.className = `bot-note ${live.length ? 'good' : 'warn'}`;
    note.innerHTML = (live.length
      ? `Trading real money now: <strong>${escHtml(live.join(', '))}</strong>. `
      : '<strong>No strategy is trading real money right now.</strong> ') +
      'Each strategy moves between paper and real money by itself, from its own results. ' +
      'The table below holds every real trade; the dip strategy\'s 46 trades from before 10 Nov 2026 are its old record. ' +
      'See the <a href="#" data-goto="paper">Strategies</a> tab for where each one stands.';
    note.querySelectorAll('[data-goto]').forEach(a => a.addEventListener('click', e => { e.preventDefault(); setBotTab(a.dataset.goto); }));
  }
}

// ── tabs ──────────────────────────────────────────────────────────────────────
function setBotTab(name) {
  bot.tab = name;
  document.querySelectorAll('.bot-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.bot-pane').forEach(p => { p.style.display = p.dataset.pane === name ? '' : 'none'; });
  if (name === 'paper') renderPaper();
  if (name === 'why') renderWhy();
  if (name === 'system') renderSystem();
}

const loadingRow = msg => `<div class="loading-row"><span class="spinner"></span> ${msg}</div>`;
const errorRow = e => `<div class="perf-empty">⚠ Could not load: ${escHtml(e.message)}<br>` +
  '<span>If this says 404 or 401, the database update has not been applied yet.</span></div>';
const coinOf = sym => escHtml(String(sym || '').replace('-USDT', ''));
const when = iso => new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });

// ── paper strategies ──────────────────────────────────────────────────────────
async function renderPaper(force = false) {
  const body = el('paperBody');
  if (!bot.paper || force) body.innerHTML = loadingRow('Loading paper trades…');
  let rows;
  try { rows = await loadPaper(force); } catch (e) { body.innerHTML = errorRow(e); return; }
  const stats = paperStats(rows);
  const data = (bot.status || {}).data || {};
  const lad = data.ladder || null;
  const modes = data.modes || {};
  const names = [...new Set([...Object.keys(STRATEGY_INFO), ...Object.keys(stats),
                             ...Object.keys((lad || {}).strategies || modes)])];
  const openLive = ((data.open || {}).live_by || {});

  const cards = names.map(k => {
    const info = STRATEGY_INFO[k] || { name: k, what: '' };
    const p = stats[k] || paperStats([{ strategy: k, closed_at: null }])[k];
    const openPaper = stats[k] ? p.open : 0;
    // The worker's ladder numbers when it has reported them (real + paper trades since the
    // record began); otherwise this page's own count of the paper trades.
    const L = lad && lad.strategies && lad.strategies[k];
    const s = L ? L : { n: p.n, wins: p.wins, mean: p.avg, pf: p.pf, se: p.se, day_mean: p.avg, days: p.n,
                        n_live: 0, n_paper: p.n };
    const level = L ? L.level : (modes[k] === 'live' ? 'full' : modes[k] || 'paper');
    const v = ladderView(s, level, (lad || {}).bar || {}, L ? L.stake : null);
    const chip = v.live ? `REAL · ${level === 'trial' ? `trial $${L ? L.stake : 20}` : 'full'}` : level;
    return `<div class="strat-card">
      <div class="strat-head"><strong>${escHtml(info.name)}</strong>
        <span class="bot-chip mode-${v.live ? 'live' : escHtml(level)}">${escHtml(chip)}</span></div>
      <div class="strat-what">${escHtml(info.what)}</div>
      <div class="perf-stats">
        <div class="perf-stat"><span class="ps-label">Closed trades</span><span class="ps-value">${s.n}${s.n ? ` <small>(${s.wins} won)</small>` : ''}</span></div>
        <div class="perf-stat"><span class="ps-label">Average per trade</span><span class="ps-value ${s.mean > 0 ? 'pos' : s.mean < 0 ? 'neg' : ''}">${s.n ? fmtPct(s.mean) : '—'}</span></div>
        <div class="perf-stat"><span class="ps-label">Profit factor</span><span class="ps-value">${s.pf === null || s.pf === undefined ? '—' : (s.pf === Infinity || s.pf >= 999) ? '∞' : s.pf.toFixed(2)}</span></div>
        <div class="perf-stat"><span class="ps-label">Open now</span><span class="ps-value">${openPaper + (+openLive[k] || 0)}</span></div>
      </div>
      <ul class="checks">${v.checks.map(c => `<li class="${c.ok ? 'ok' : 'no'}">${c.ok ? '✓' : '○'} ${escHtml(c.text)}</li>`).join('')}</ul>
      <div class="bot-note ${v.cls}"><strong>${escHtml(v.verdict)}</strong>${v.next ? `<br>${escHtml(v.next)}` : ''}</div>
    </div>`;
  }).join('');

  const open = rows.filter(t => !t.closed_at);
  const closed = rows.filter(t => t.closed_at).sort((a, b) => new Date(b.closed_at) - new Date(a.closed_at)).slice(0, 40);
  const plan = t => t.kind === 'option3' ? `TP +${t.params.tp}% · SL −${t.params.sl}% · trail ${t.params.trail}%`
    : t.kind === 'hold' ? `hold ${t.params.hold_hours}h, then sell` : 'until the trend ends';
  const exitNames = { sl: 'Stop loss', be_stop: 'Break-even stop', tp_trail: 'Target + trailing', time: 'Held full time', rule: 'Trend ended', stop: 'Far stop' };

  body.innerHTML = `
    <p class="bot-intro">Every strategy stands on one rung: <strong>paper</strong> (live prices, same fees and slippage, no money),
      a <strong>$20 real-money trial</strong>, or <strong>full size</strong>. It moves up or down by itself as its own record —
      real and paper trades together, since 10 Nov 2026 — passes or fails the four checks below. The bar was set before any result existed.
      Real trades appear in the Live trades tab; paper trades are listed below.</p>
    <div class="strat-grid">${cards}</div>
    <h4 class="bot-h">Open paper trades</h4>
    ${open.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Coin</th><th>Strategy</th><th>Opened</th><th class="num">Entry</th><th class="num">Stake</th><th>Plan</th></tr></thead>
      <tbody>${open.map(t => `<tr><td>${coinOf(t.symbol)}</td><td>${escHtml((STRATEGY_INFO[t.strategy] || { name: t.strategy }).name)}</td>
        <td>${when(t.opened_at)}</td><td class="num">${Number((+t.entry_price).toPrecision(5))}</td><td class="num">$${(+t.amount_usdt).toFixed(0)}</td><td>${escHtml(plan(t))}</td></tr>`).join('')}</tbody></table></div>`
      : '<div class="perf-empty">None open.</div>'}
    <h4 class="bot-h">Closed paper trades</h4>
    ${closed.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Coin</th><th>Strategy</th><th>Closed</th><th>Exit</th><th class="num">Result</th><th class="num">On the stake</th><th class="num" title="Worst and best point reached while the trade was open">Worst / best</th></tr></thead>
      <tbody>${closed.map(t => `<tr><td>${coinOf(t.symbol)}</td><td>${escHtml((STRATEGY_INFO[t.strategy] || { name: t.strategy }).name)}</td>
        <td>${when(t.closed_at)}</td><td>${escHtml(exitNames[t.exit_reason] || t.exit_reason || '')}</td>
        <td class="num ${+t.net_pct > 0 ? 'pos' : 'neg'}">${fmtPct(+t.net_pct || 0)}</td>
        <td class="num ${+t.net_pnl_usdt > 0 ? 'pos' : 'neg'}">${fmtSigned(+t.net_pnl_usdt || 0)}</td>
        <td class="num">${t.mae_pct == null ? '—' : `${fmtPct(+t.mae_pct, 1)} / ${fmtPct(+t.mfe_pct, 1)}`}</td></tr>`).join('')}</tbody></table></div>`
      : '<div class="perf-empty">No paper trade has closed yet.<br><span>The first ones appear here as soon as a strategy takes a signal.</span></div>'}`;
}

// ── why no trade? ─────────────────────────────────────────────────────────────
async function renderWhy(force = false) {
  const body = el('whyBody');
  if (!bot.refused || force) body.innerHTML = loadingRow('Loading the decision record…');
  let rows;
  try { await loadBotStatus(force); rows = await loadRefused(force); } catch (e) { body.innerHTML = errorRow(e); return; }
  const f = funnelTotals(((bot.status || {}).data || {}).funnel);
  const gates = gateScore(rows);

  let headline;
  if (!f.days || !f.scans) {
    headline = 'No scan record yet. It starts filling as soon as the VPS runs the latest version.';
  } else {
    const phrase = { regime: 'the BTC downtrend filter', max_open: 'the open-trade cap', breaker: 'the stop-loss circuit breaker',
                     loss_limit: 'the monthly loss limit', correlation: 'the correlation guard', cap: 'ranking below the top pick',
                     funding: 'crowded funding', ai: 'the AI advisor' };
    const parts = f.stopped.map(x => `<strong>${x.n}</strong> by ${escHtml(phrase[x.gate] || x.gate)}`);
    headline = `Last ${f.days} day${f.days > 1 ? 's' : ''}: <strong>${f.candidates}</strong> setup${f.candidates === 1 ? '' : 's'} qualified. ` +
      (parts.length ? `Stopped: ${parts.join(', ')}. ` : '') +
      `<strong>${f.taken}</strong> taken${f.paper ? ` (${f.paper} on paper)` : ''}.` +
      (f.bearShare !== null ? ` BTC was in a downtrend <strong>${Math.round(f.bearShare * 100)}%</strong> of that time.` : '');
  }

  const max = Math.max(1, f.strong_buy || 0);
  const bar = (label, n, cls = '', hint = '') => `<div class="funnel-row" title="${escHtml(hint)}">
      <span class="fl">${escHtml(label)}</span>
      <span class="fb"><span class="fbar ${cls}" style="width:${Math.max(n ? 2 : 0, n / max * 100).toFixed(1)}%"></span></span>
      <span class="fn">${n.toLocaleString()}</span></div>`;
  const funnel = !f.scans ? '' : `
    <h4 class="bot-h">Where signals stop</h4>
    <div class="funnel">
      ${bar('STRONG BUY readings', f.strong_buy, '', 'Coin-checks that read STRONG BUY. The same hourly candle is checked about 60 times, so this counts checks, not coins.')}
      ${bar('… no 30-minute reversal yet', f.no_reversal, 'dim', 'Still falling: the last 30-minute candle was not green with rising RSI and volume.')}
      ${bar('… hourly volume under 2×', f.low_volume, 'dim', 'The signal candle did not have twice the usual volume.')}
      ${bar('… same coin again within 4h', f.cooldown, 'dim', 'A coin that already qualified waits 4 hours before it can qualify again.')}
    </div>
    <div class="funnel">
      ${bar('Qualified setups', f.candidates, 'blue', 'Distinct setups that passed every signal rule.')}
      ${f.stopped.map(x => bar(`… stopped: ${GATE_INFO[x.gate] || x.gate}`, x.n, 'amber')).join('')}
      ${bar('Taken', f.taken, 'green', 'Real trades plus paper trades.')}
    </div>`;

  const days = !f.list.length ? '' : `
    <h4 class="bot-h">Day by day</h4>
    <div class="table-wrap"><table class="data-table"><thead><tr><th>Day (UTC)</th><th class="num">BTC downtrend</th><th class="num">Qualified</th><th class="num">Stopped by downtrend</th><th class="num">AI said no</th><th class="num">Taken</th></tr></thead>
    <tbody>${f.list.slice().reverse().map(d => `<tr><td>${escHtml(d.date)}</td>
      <td class="num">${d.scans ? Math.round((d.bear_scans || 0) / d.scans * 100) + '%' : '—'}</td>
      <td class="num">${d.candidates || 0}</td><td class="num">${d.regime || 0}</td><td class="num">${d.ai_skip || 0}</td>
      <td class="num">${(d.traded || 0) + (d.paper || 0)}</td></tr>`).join('')}</tbody></table></div>`;

  const order = Object.keys(gates).sort((a, b) => gates[b].n - gates[a].n);
  const score = !order.length ? '' : `
    <h4 class="bot-h">Was saying no the right call?</h4>
    <p class="bot-intro">24 hours after each refusal the bot checks what the price did: would the trade have hit its stop first (the “no” saved money) or its target first (the “no” cost a win)?</p>
    <div class="table-wrap"><table class="data-table"><thead><tr><th>Who said no</th><th class="num">Refused</th><th class="num">Checked</th><th class="num">Would have lost</th><th class="num">Would have won</th><th class="num">Neither</th></tr></thead>
    <tbody>${order.map(g => { const x = gates[g]; return `<tr><td>${escHtml(GATE_INFO[g] || g)}</td><td class="num">${x.n}</td><td class="num">${x.graded}</td>
      <td class="num pos">${x.good}</td><td class="num neg">${x.missed}</td><td class="num">${x.neutral}</td></tr>`; }).join('')}</tbody></table></div>`;

  const verdictCell = fu => {
    const v = fu && fu.verdict;
    if (!v) return '<span class="muted">⏳ in 24h</span>';
    if (v === 'good_skip') return '<span class="pos">✓ would have lost</span>';
    if (v === 'missed_win') return '<span class="neg">✗ would have won</span>';
    return '<span class="muted">— neither</span>';
  };
  const list = !rows.length
    ? '<div class="perf-empty">No refused signal on record yet.<br><span>Every qualified setup that is not traded will be listed here, with who refused it and why.</span></div>'
    : `<div class="table-wrap" style="max-height:340px"><table class="data-table"><thead><tr><th>When</th><th>Coin</th><th>Who said no</th><th>Why</th><th>24h later</th></tr></thead>
      <tbody>${rows.slice(0, 80).map(r => `<tr><td>${when(r.created_at)}</td><td>${coinOf(r.symbol)}</td>
        <td>${escHtml(GATE_INFO[r.gate || 'ai'] || r.gate)}</td><td class="why-cell" title="${escHtml(r.reason || '')}">${escHtml(r.reason || '')}</td>
        <td>${verdictCell(r.followup)}</td></tr>`).join('')}</tbody></table></div>`;

  body.innerHTML = `<div class="bot-note">${headline}</div>${funnel}${days}${score}
    <h4 class="bot-h">Signals that qualified and were not traded</h4>${list}`;
}

// ── system ────────────────────────────────────────────────────────────────────
async function renderSystem(force = false) {
  const body = el('systemBody');
  const s = await loadBotStatus(force);
  const d = s.data || {};
  const h = statusHealth(s.updated_at);
  const yes = v => v ? '<span class="pos">yes</span>' : '<span class="neg">NO</span>';
  const row = (k, v) => `<tr><td>${k}</td><td class="num">${v}</td></tr>`;
  const scanAge = d.scan && d.scan.at ? ageText(Date.now() - d.scan.at * 1000) : '—';
  const openPaper = Object.entries((d.open || {}).paper || {}).map(([k, n]) => `${escHtml(k)} ${n}`).join(', ') || 'none';
  body.innerHTML = s.updated_at ? `
    <div class="table-wrap"><table class="data-table"><tbody>
      ${row('Last report from the VPS', `${ageText(h.age)} <small>(${when(s.updated_at)})</small>`)}
      ${row('Last market scan', scanAge)}
      ${row('Code version', escHtml(d.version || '—'))}
      ${row('Coins scanned', d.scan ? `${d.scan.coins ?? '—'} <small>(${d.scan.thin ?? 0} skipped as too thin on OKX)</small>` : '—')}
      ${row('Market regime', escHtml((d.regime || {}).msg || '—'))}
      ${row('Coins oversold right now', d.breadth_os == null ? '—' : Math.round(d.breadth_os * 100) + '%')}
      ${row('Open live trades', (d.open || {}).live ?? 0)}
      ${row('Open paper trades', openPaper)}
      ${row('OKX key on the VPS', yes((d.keys || {}).okx))}
      ${row('AI key on the VPS', yes((d.keys || {}).claude))}
      ${row('Telegram on the VPS', yes((d.keys || {}).telegram))}
      ${row('Database key', (d.keys || {}).db_role === 'service_role' || (d.keys || {}).db_role === 'secret' ? '<span class="pos">private (correct)</span>' : `<span class="neg">${escHtml((d.keys || {}).db_role || 'unknown')}</span>`)}
      ${row('Risk per live trade', d.limits ? `${d.limits.risk_pct}% of the account` : '—')}
      ${row('Monthly loss limit', d.limits ? `${d.limits.loss_30d_pct}% of the account` : '—')}
      ${row('Max open trades', d.limits ? d.limits.max_open : '—')}
      ${row('STRONG BUY score', d.limits ? d.limits.score : '—')}
    </tbody></table></div>`
    : '<div class="perf-empty">The bot has not reported yet.<br><span>It starts as soon as the VPS runs the latest version.</span></div>';
}

// ── wiring ────────────────────────────────────────────────────────────────────
function initBotPanel() {
  if (!bot.wired) {
    bot.wired = true;
    document.querySelectorAll('.bot-tab').forEach(b => b.addEventListener('click', () => setBotTab(b.dataset.tab)));
    el('perfRefreshBtn').addEventListener('click', () => {
      loadBotStatus(true).then(() => {
        if (bot.tab === 'paper') renderPaper(true);
        if (bot.tab === 'why') renderWhy(true);
        if (bot.tab === 'system') renderSystem(true);
      });
    });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) loadBotStatus(); });
  }
  loadBotStatus(true);
  clearInterval(bot.timer);
  // One small row every 5 minutes, and only while the page is actually being looked at.
  bot.timer = setInterval(() => { if (!document.hidden) loadBotStatus(true); }, 5 * 60000);
}
