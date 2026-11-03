// Dashboard settings encryption and the manual-trading switch, lifted out of app.js and
// run against an in-memory Supabase row. No network.
//     node tests/test_dashboard_settings.js
const fs = require('fs');
const path = require('path');
const crypto = globalThis.crypto;
const ROOT = path.join(__dirname, '..');
const src = fs.readFileSync(path.join(ROOT, 'app.js'), 'utf8');
const lines = src.split(/\r?\n/);
function fn(header) {
  const i = lines.findIndex(l => l.startsWith(header));
  if (i < 0) throw new Error('missing ' + header);
  let j = i; while (lines[j] !== '}') j++;
  return lines.slice(i, j + 1).join('\n');
}
const consts = lines.filter(l => /^const (LEGACY_)?PBKDF2_ITERATIONS = /.test(l)).join('\n');

let stored = null, toasts = [], listenersAttached = 0;
const CONFIG = { CLAUDE_API_KEY: 'sk-claude', OKX_API_KEY: 'okx-key', OKX_SECRET_KEY: 'okx-secret',
                 OKX_PASSPHRASE: 'okx-pass', TELEGRAM_BOT_TOKEN: 'tg', TELEGRAM_CHAT_ID: '1', MANUAL_TRADING: false };
const state = { tickers: {} };
const toast = (m) => toasts.push(m);
const isSupabaseConfigured = () => true;
const getSupabaseCfg = () => ({ url: 'https://sb.test' });
const sbHeaders = (x) => ({ ...(x || {}) });
const fetch = async (url, opts = {}) => {
  if (opts.method === 'POST') { stored = JSON.parse(opts.body); return { ok: true, json: async () => ({}) }; }
  return { ok: true, json: async () => (stored ? [stored] : []) };
};
const elements = {};
const el = id => (elements[id] ||= { id, style: {}, innerHTML: '', textContent: '', disabled: false,
  addEventListener() {}, querySelectorAll(sel) { if (sel === '.btn-take-action') listenersAttached++; return []; } });
const document = { getElementById: id => el(id) };
const markdownToHtml = t => t;

eval([consts, fn('function randomHex'), fn('async function deriveKey'), fn('async function encryptJSON'),
      fn('async function decryptJSON'), fn('async function saveToCloud'), fn('async function loadFromCloud'),
      fn('function remindCloudResave'), fn('function parseTradeActions'), fn('function renderAiResponse'),
      fn('function showTradeConfirmation'), fn('async function executeTrade')].join('\n\n'));

const ok = (c, m) => { if (!c) { console.error('FAIL', m); process.exit(1); } console.log('ok  ', m); };
const sha256hex = async s => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s))))
  .map(b => b.toString(16).padStart(2, '0')).join('');
const fails = async (p, msg) => { try { await p; return false; } catch (e) { return e.message === msg; } };

(async () => {
  // A row exactly as the dashboard saved it before 2026-11-03: fast hash + 100k rounds.
  const oldPw = 'old password', salt = randomHex(16);
  const payload = { claude: 'sk-claude', okxKey: 'okx-key', okxSecret: 'okx-secret', okxPassphrase: 'okx-pass', tgToken: 'tg', tgChatId: '1' };
  const old = await encryptJSON(payload, oldPw, salt, 100_000);
  stored = { id: 'main', password_hash: await sha256hex(oldPw + salt), encrypted_data: old.encrypted, iv: old.iv, salt };

  const got = await loadFromCloud(oldPw);
  ok(JSON.stringify(got) === JSON.stringify(payload), 'your current (old-format) settings still open with the right password');
  ok(state.cloudLegacy === true, 'and are recognised as old format');
  remindCloudResave();
  ok(toasts.length === 1 && /Save All to Cloud/.test(toasts[0]), 'unlocking them shows the one-time "re-save" reminder');
  ok(await fails(loadFromCloud('wrong password'), 'Incorrect password.'), 'a wrong password is refused');

  const newPw = 'correct horse battery staple';
  const t0 = Date.now();
  await saveToCloud(newPw);
  const saveMs = Date.now() - t0;
  ok(stored.password_hash === 'pbkdf2-600000', `a new save stores only a format marker ("${stored.password_hash}")`);
  const fp = await sha256hex(newPw + stored.salt);
  ok(!JSON.stringify(stored).includes(fp) && !JSON.stringify(stored).includes(newPw), 'and no password fingerprint and no password anywhere in the row');
  ok(stored.salt !== salt && stored.iv && stored.encrypted_data, 'with a fresh salt and IV');

  toasts = []; state.cloudLegacy = undefined;
  const t1 = Date.now();
  const back = await loadFromCloud(newPw);
  const loadMs = Date.now() - t1;
  ok(JSON.stringify(back) === JSON.stringify(payload) && state.cloudLegacy === false, 'the new row opens with the new password');
  remindCloudResave();
  ok(toasts.length === 0, 'and no reminder after the re-save');
  ok(await fails(loadFromCloud(oldPw), 'Incorrect password.'), 'the old password no longer opens it');
  console.log(`      (600 000 rounds here: save ${saveMs} ms, unlock ${loadMs} ms)`);

  // Manual trading switch
  toasts = []; listenersAttached = 0;
  renderAiResponse('Buy SOL here. [TRADE:{"side":"buy","symbol":"SOL-USDT","amountUsdt":50,"partialTpPct":3,"trailingCallbackPct":1.5,"slPct":4}]');
  const html = elements.aiResponseArea.innerHTML;
  ok(/<button class="btn-take-action [^"]*" data-idx="0" disabled>/.test(html) && /Manual trading from the dashboard is off/.test(html),
     'the AI suggestion shows as a disabled button with the "use the OKX app" note');
  ok(listenersAttached === 0, 'no click handler is attached to it');
  showTradeConfirmation({ side: 'buy', symbol: 'SOL-USDT' });
  await executeTrade({ side: 'buy', symbol: 'SOL-USDT', amountUsdt: 50 }, null);
  ok(toasts.length === 2 && toasts.every(t => /Manual trading from the dashboard is off/.test(t)),
     'and both entry points refuse before anything reaches OKX');

  CONFIG.MANUAL_TRADING = true; listenersAttached = 0;
  renderAiResponse('[TRADE:{"side":"buy","symbol":"SOL-USDT","amountUsdt":50,"partialTpPct":3,"trailingCallbackPct":1.5,"slPct":4}]');
  ok(!/ disabled>/.test(elements.aiResponseArea.innerHTML) && listenersAttached === 1, 'turning MANUAL_TRADING back on restores the button');
  console.log('ALL PASSED');
})();
