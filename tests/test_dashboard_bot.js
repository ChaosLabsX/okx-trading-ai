// The bot panel (bot.js): its arithmetic, and that every tab renders from realistic rows
// without throwing. No network: fetch is replaced by an in-memory Supabase.
//     node tests/test_dashboard_bot.js            run the checks
//     node tests/test_dashboard_bot.js --preview  also write tests/_preview/bot.html (open it to look at the layout)
const fs = require('fs');
const path = require('path');
const ROOT = path.join(__dirname, '..');
const lib = require(path.join(ROOT, 'bot.js'));
let n = 0;
const ok = (cond, what) => { if (!cond) throw new Error('FAILED: ' + what); n++; console.log('ok   ' + what); };

// ── arithmetic ───────────────────────────────────────────────────────────────
const closed = (strategy, pct, usd = pct) => ({ strategy, closed_at: '2026-11-12T00:00:00Z', net_pct: pct, net_pnl_usdt: usd });
let s = lib.paperStats([closed('dip', 2), closed('dip', -1), closed('dip', 3), { strategy: 'dip', closed_at: null }]).dip;
ok(s.n === 3 && s.wins === 2 && s.open === 1 && Math.abs(s.avg - 4 / 3) < 1e-9 && Math.abs(s.pf - 5) < 1e-9,
   'paper stats: 3 closed (2 won), 1 open, average +1.33%, profit factor 5');
ok(s.verdict === 'wait' && !s.checks[0].ok && s.checks[1].ok, 'three good trades are NOT enough: verdict "not enough evidence"');

const many = (k, win, loss, wins, losses) => [...Array(wins).fill(0).map(() => closed(k, win)), ...Array(losses).fill(0).map(() => closed(k, loss))];
s = lib.paperStats(many('flush', 4, -3, 20, 14)).flush;
ok(s.n === 34 && s.checks.every(c => c.ok) && s.verdict === 'pass', `34 trades, +1.12% average, PF ${s.pf.toFixed(2)}: passes all four checks`);
s = lib.paperStats(many('flush', 3, -3, 16, 15)).flush;
ok(s.checks[0].ok && s.checks[1].ok && !s.checks[2].ok && !s.checks[3].ok && s.verdict === 'wait',
   '31 trades barely above zero (PF 1.07): positive, but fails the profit-factor and the luck check');
s = lib.paperStats(many('dip', 2.5, -3.6, 15, 17)).dip;
ok(s.verdict === 'fail', '32 trades that lose on average: verdict "failing"');
ok(lib.paperStats([]).dip === undefined && Object.keys(lib.paperStats(null)).length === 0, 'no rows: no strategies, no crash');

const funnel = {
  '2026-11-08': { scans: 300, bear_scans: 300, strong_buy: 900, no_reversal: 600, low_volume: 80, cooldown: 100, candidates: 47, regime: 47 },
  '2026-11-09': { scans: 300, bear_scans: 250, strong_buy: 200, no_reversal: 150, candidates: 7, regime: 5, ai_skip: 1, paper: 1 },
  '2026-11-10': { scans: 150, bear_scans: 50 },
  '2099-01-01': { scans: 999, candidates: 999 },
};
const f = lib.funnelTotals(funnel, 14, '2026-11-10');
ok(f.days === 3 && f.candidates === 54 && f.regime === 52 && f.taken === 1 && f.paper === 1, 'funnel: 54 qualified, 52 stopped by the downtrend filter, 1 taken (future-dated rows ignored)');
ok(Math.abs(f.bearShare - 600 / 750) < 1e-9 && f.stopped[0].gate === 'regime' && f.stopped[1].gate === 'ai',
   'downtrend share 80% of scans; blockers sorted biggest first, ai_skip shown as the AI');
ok(lib.funnelTotals(undefined).days === 0 && lib.funnelTotals({}).bearShare === null, 'empty funnel: zero days, no division by zero');

const g = lib.gateScore([
  { gate: 'regime', followup: { verdict: 'good_skip' } }, { gate: 'regime', followup: { verdict: 'good_skip' } },
  { gate: 'regime', followup: { verdict: 'missed_win' } }, { gate: 'regime', followup: null },
  { gate: 'ai', followup: { verdict: 'neutral_skip' } }, { followup: { verdict: 'missed_win' } }, { gate: 'ai', followup: { verdict: 'ungradable' } },
]);
ok(g.regime.n === 4 && g.regime.graded === 3 && g.regime.good === 2 && g.regime.missed === 1, 'gate score: downtrend filter 4 refusals, 3 checked, 2 would have lost, 1 would have won');
ok(g.ai.n === 3 && g.ai.graded === 2 && g.ai.missed === 1 && g.ai.neutral === 1, 'rows without a gate count as the AI; ungradable rows are not counted as checked');

const now = Date.parse('2026-11-10T12:00:00Z');
ok(lib.statusHealth('2026-11-10T11:56:00Z', now).level === 'ok' && lib.statusHealth('2026-11-10T11:30:00Z', now).level === 'late'
   && lib.statusHealth('2026-11-10T09:00:00Z', now).level === 'down' && lib.statusHealth(null, now).level === 'unknown',
   'health: 4 min = running, 30 min = late, 3 h = not reporting, never = unknown');
ok(lib.ageText(20000) === 'just now' && lib.ageText(5 * 60000) === '5 min ago' && lib.ageText(125 * 60000) === '2h 5m ago' && lib.ageText(null) === 'never', 'age text');

// ── rendering against an in-memory Supabase ───────────────────────────────────
const iso = (d, h = 0) => `2026-11-${String(d).padStart(2, '0')}T${String(h).padStart(2, '0')}:10:00+00:00`;
const DB = {
  bot_status: [{ updated_at: new Date(Date.now() - 3 * 60000).toISOString(), data: {
    version: 'abc1234', test_mode: false, modes: { dip: 'paper', flush: 'paper', trend: 'paper' },
    scan: { at: Date.now() / 1000 - 90, coins: 55, thin: 12 },
    regime: { ok: false, msg: 'BTC $74,833 < 4H EMA50 $76,489 and 4H RSI 42 — bearish regime, dip-buys blocked' },
    breadth_os: 0.2, open: { live: 0, paper: { flush: 2, trend: 1 } },
    keys: { okx: true, claude: true, telegram: true, db_role: 'service_role' },
    limits: { risk_pct: 1, loss_30d_pct: 6, max_open: 3, score: 4 }, funnel, run_secs: 262 } }],
  paper_trades: [
    { id: 9, strategy: 'flush', symbol: 'SOL-USDT', kind: 'hold', opened_at: iso(9, 16), entry_price: 141.2, amount_usdt: 100, params: { hold_hours: 48 }, closed_at: null },
    { id: 8, strategy: 'flush', symbol: 'LINK-USDT', kind: 'hold', opened_at: iso(9, 16), entry_price: 12.31, amount_usdt: 100, params: { hold_hours: 48 }, closed_at: null },
    { id: 7, strategy: 'trend', symbol: 'TRX-USDT', kind: 'rule', opened_at: iso(8), entry_price: 0.3312, amount_usdt: 100, params: { ema: 50, band: 0.02 }, closed_at: null },
    { id: 6, strategy: 'dip', symbol: 'AR-USDT', kind: 'option3', opened_at: iso(6, 4), entry_price: 6.12, amount_usdt: 131.5, params: { tp: 3.1, sl: 3.9, trail: 1.5 },
      closed_at: iso(6, 19), exit_price: 6.31, exit_reason: 'tp_trail', net_pnl_usdt: 3.42, net_pct: 2.6, mae_pct: -1.1, mfe_pct: 4.4 },
    { id: 5, strategy: 'flush', symbol: 'ENA-USDT', kind: 'hold', opened_at: iso(5, 17), entry_price: 0.2841, amount_usdt: 100, params: { hold_hours: 48 },
      closed_at: iso(7, 17), exit_price: 0.2702, exit_reason: 'time', net_pnl_usdt: -5.19, net_pct: -5.19, mae_pct: -11.2, mfe_pct: 3.1 },
    { id: 4, strategy: 'flush', symbol: '<img src=x onerror=alert(1)>', kind: 'hold', opened_at: iso(5, 17), entry_price: 1, amount_usdt: 100, params: { hold_hours: 48 },
      closed_at: iso(7, 17), exit_price: 1.07, exit_reason: 'time', net_pnl_usdt: 6.7, net_pct: 6.7, mae_pct: -6.0, mfe_pct: 9.3 },
  ],
  skipped_setups: [
    { id: 3, symbol: 'JUP-USDT', price: 0.41, reason: 'bearish BTC regime', gate: 'regime', created_at: iso(8, 14), followup: { verdict: 'good_skip' } },
    { id: 2, symbol: 'NEAR-USDT', price: 2.2, reason: 'RSI 4H 68 with resistance 1.1% above — poor reward <script>x</script>', gate: 'ai', created_at: iso(9, 8), followup: { verdict: 'missed_win' } },
    { id: 1, symbol: 'OP-USDT', price: 0.9, reason: 'bearish BTC regime', gate: 'regime', created_at: iso(9, 9), followup: null },
  ],
};
const els = {};
const el = id => (els[id] ||= { id, style: {}, innerHTML: '', className: '', dataset: {}, classList: { toggle() {}, add() {}, remove() {} },
  addEventListener() {}, querySelectorAll() { return []; } });
const document = { hidden: false, querySelectorAll: () => [], addEventListener() {} };
const escHtml = str => String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const fmtSigned = v => `${v >= 0 ? '+' : '−'}$${Math.abs(v).toFixed(2)}`;
const getSupabaseCfg = () => ({ url: 'https://sb.test' });
const sbHeaders = () => ({});
let reads = [];
const fetch = async url => {
  const table = url.split('/rest/v1/')[1].split('?')[0];
  reads.push(table);
  return { ok: true, json: async () => DB[table] };
};
const state = {};
// `const` inside eval stays inside it; the test needs to reach `bot` to reset its cache.
eval(fs.readFileSync(path.join(ROOT, 'bot.js'), 'utf8').replace("'use strict';", '')
  .replace(/if \(typeof module[\s\S]*?\n}\n/, '').replace('const bot = {', 'var bot = {'));

(async () => {
  await loadBotStatus(true);
  ok(/Bot running/.test(el('botStrip').innerHTML) && /BTC downtrend/.test(el('botStrip').innerHTML)
     && /Dip buy: paper/.test(el('botStrip').innerHTML), 'status strip: running, downtrend, dip on paper');
  ok(/No strategy is trading real money/.test(el('liveModeNote').innerHTML), 'live tab says plainly that nothing trades real money');
  await renderPaper();
  let h = el('paperBody').innerHTML;
  ok(/Liquidation flush/.test(h) && /Not enough evidence yet/.test(h) && /SOL/.test(h) && /hold 48h/.test(h), 'paper tab: strategy cards, verdicts, open trades with their plan');
  ok(!/<img src=x/.test(h) && /&lt;img/.test(h), 'a hostile symbol is escaped, not rendered');
  await renderWhy();
  h = el('whyBody').innerHTML;
  ok(/<strong>54<\/strong> setups qualified/.test(h) && /<strong>52<\/strong> by the BTC downtrend filter/.test(h) && /80%/.test(h),
     'why tab: "54 setups qualified, 52 stopped by the BTC downtrend filter, downtrend 80% of the time"');
  ok(/would have lost/.test(h) && /would have won/.test(h) && /in 24h/.test(h) && !/<script>x/.test(h), 'refused signals with their 24h verdicts; reason text escaped');
  await renderSystem();
  ok(/abc1234/.test(el('systemBody').innerHTML) && /private \(correct\)/.test(el('systemBody').innerHTML), 'system tab: version and database key');
  const before = reads.length;
  await renderPaper(); await renderWhy();
  ok(reads.length === before, 'switching back to a tab does not fetch again');

  DB.bot_status = []; DB.paper_trades = []; DB.skipped_setups = [];
  bot.paper = bot.refused = null;
  await loadBotStatus(true); await renderPaper(); await renderWhy(); await renderSystem();
  ok(/has not reported yet/.test(el('botStrip').innerHTML) && /No paper trade has closed yet/.test(el('paperBody').innerHTML)
     && /No scan record yet/.test(el('whyBody').innerHTML), 'empty database (before the VPS update): every tab explains itself instead of breaking');

  if (process.argv.includes('--preview')) {
    DB.bot_status = [{ updated_at: new Date(Date.now() - 3 * 60000).toISOString(), data: {
      version: 'abc1234', modes: { dip: 'paper', flush: 'paper', trend: 'paper' }, scan: { at: Date.now() / 1000 - 90, coins: 55, thin: 12 },
      regime: { ok: false, msg: 'BTC bearish' }, breadth_os: 0.2, open: { live: 0, paper: { flush: 2, trend: 1 } },
      keys: { okx: true, claude: true, telegram: true, db_role: 'service_role' }, limits: { risk_pct: 1, loss_30d_pct: 6, max_open: 3, score: 4 }, funnel } }];
    DB.paper_trades = [
      { id: 9, strategy: 'flush', symbol: 'SOL-USDT', kind: 'hold', opened_at: iso(9, 16), entry_price: 141.2, amount_usdt: 100, params: { hold_hours: 48 }, closed_at: null },
      { id: 7, strategy: 'trend', symbol: 'TRX-USDT', kind: 'rule', opened_at: iso(8), entry_price: 0.3312, amount_usdt: 100, params: {}, closed_at: null },
      { id: 6, strategy: 'dip', symbol: 'AR-USDT', kind: 'option3', opened_at: iso(6, 4), entry_price: 6.12, amount_usdt: 131.5, params: { tp: 3.1, sl: 3.9, trail: 1.5 },
        closed_at: iso(6, 19), exit_reason: 'tp_trail', net_pnl_usdt: 3.42, net_pct: 2.6, mae_pct: -1.1, mfe_pct: 4.4 },
      { id: 5, strategy: 'flush', symbol: 'ENA-USDT', kind: 'hold', opened_at: iso(5, 17), entry_price: 0.2841, amount_usdt: 100, params: { hold_hours: 48 },
        closed_at: iso(7, 17), exit_reason: 'time', net_pnl_usdt: -5.19, net_pct: -5.19, mae_pct: -11.2, mfe_pct: 3.1 }];
    DB.skipped_setups = [
      { id: 3, symbol: 'JUP-USDT', reason: 'bearish BTC regime', gate: 'regime', created_at: iso(8, 14), followup: { verdict: 'good_skip' } },
      { id: 2, symbol: 'NEAR-USDT', reason: 'RSI 4H 68 with resistance 1.1% above — poor reward for the risk', gate: 'ai', created_at: iso(9, 8), followup: { verdict: 'missed_win' } },
      { id: 1, symbol: 'OP-USDT', reason: 'bearish BTC regime', gate: 'regime', created_at: iso(9, 9), followup: null }];
    bot.paper = bot.refused = null;
    await loadBotStatus(true); await renderPaper(); await renderWhy(); await renderSystem();
    const css = fs.readFileSync(path.join(ROOT, 'style.css'), 'utf8');
    const pane = (title, id) => `<div class="bot-pane"><h3 style="color:#94a3b8;font:600 13px Inter,sans-serif;margin:26px 0 8px">TAB: ${title}</h3>${el(id).innerHTML}</div>`;
    const html = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${css}</style>
<body style="padding:14px"><section class="card perf-card open" style="max-width:1100px;margin:0 auto">
<div class="card-header"><h2 class="card-title">Bot Performance</h2></div>
<div class="bot-strip">${el('botStrip').innerHTML}</div>
<div class="bot-tabs"><button class="bot-tab active">Live trades</button><button class="bot-tab">Paper strategies</button><button class="bot-tab">Why no trade?</button><button class="bot-tab">System</button></div>
<div class="bot-pane"><div class="bot-note ${el("liveModeNote").className.replace("bot-note", "")}">${el("liveModeNote").innerHTML}</div></div>
${pane('Paper strategies', 'paperBody')}${pane('Why no trade?', 'whyBody')}${pane('System', 'systemBody')}</section></body>`;
    fs.mkdirSync(path.join(__dirname, '_preview'), { recursive: true });
    fs.writeFileSync(path.join(__dirname, '_preview', 'bot.html'), html);
    console.log('preview written: tests/_preview/bot.html');
  }
  console.log(`ALL PASSED (${n} checks)`);
})().catch(e => { console.error(e.message); process.exit(1); });
