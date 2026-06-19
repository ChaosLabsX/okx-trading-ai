'use strict';

// ═══════════════════════════════════════════════════════════
//  STATE
// ═══════════════════════════════════════════════════════════
const state = {
  portfolio: [],        // [{symbol, amount, avgBuyPrice}]
  scannerSymbols: [],   // ['BTC-USDT', ...]
  tickers: {},          // {symbol: {price, change, changePercent, high24h, low24h, vol24h, source}}
  indicators: {},       // {symbol: {rsi, macd, bb, signal}}
  news: [],
  scannerFilter: 'all',
  scannerSort: 'signal',
  refreshTimer: null,
  newsTimer: null,
  isRefreshing: false,
  notifiedSignals: {},   // {symbol: lastNotifiedLabel} — prevents duplicate alerts
  usdtBalance: 0,        // free USDT from OKX sync
  sessionPassword: null, // set after successful cloud unlock
  derivData: {},         // {symbol: {fundingRate, nextFundingRate, openInterest}}
};

// ═══════════════════════════════════════════════════════════
//  LOCAL STORAGE
// ═══════════════════════════════════════════════════════════
const LS = {
  get(key, fallback = null) {
    try { const v = localStorage.getItem(key); return v !== null ? JSON.parse(v) : fallback; }
    catch { return fallback; }
  },
  set(key, val) { try { localStorage.setItem(key, JSON.stringify(val)); } catch { } },
};

// ═══════════════════════════════════════════════════════════
//  SETTINGS
// ═══════════════════════════════════════════════════════════
function loadSettings() {
  const keys = LS.get('apiKeys', {});
  if (keys.claude) CONFIG.CLAUDE_API_KEY = keys.claude;
  if (keys.okxKey) CONFIG.OKX_API_KEY = keys.okxKey;
  if (keys.okxSecret) CONFIG.OKX_SECRET_KEY = keys.okxSecret;
  if (keys.okxPassphrase) CONFIG.OKX_PASSPHRASE = keys.okxPassphrase;
  if (keys.tgToken) CONFIG.TELEGRAM_BOT_TOKEN = keys.tgToken;
  if (keys.tgChatId) CONFIG.TELEGRAM_CHAT_ID = keys.tgChatId;
  const prefs = LS.get('prefs', {});
  if (prefs.riskProfile) CONFIG.RISK_PROFILE = prefs.riskProfile;
  if (prefs.refreshInterval) CONFIG.AUTO_REFRESH_INTERVAL = parseInt(prefs.refreshInterval);
  if (prefs.tradingCapital) CONFIG.TRADING_CAPITAL = parseFloat(prefs.tradingCapital);
}

function saveSettings() {
  LS.set('supabaseCfg', {
    url: el('settingsSbUrl').value.trim(),
    key: el('settingsSbKey').value.trim(),
  });
  LS.set('apiKeys', {
    claude: el('settingsClaudeKey').value.trim(),
    okxKey: el('settingsOkxKey').value.trim(),
    okxSecret: el('settingsOkxSecret').value.trim(),
    okxPassphrase: el('settingsOkxPassphrase').value.trim(),
    tgToken: el('settingsTgToken').value.trim(),
    tgChatId: el('settingsTgChatId').value.trim(),
  });
  LS.set('prefs', {
    riskProfile: el('settingsRiskProfile').value,
    refreshInterval: el('settingsRefreshInterval').value,
    tradingCapital: el('settingsTradingCapital').value,
  });
  loadSettings();
  toast('Settings saved', 'success');
  closeModal('settingsModal');
  restartAutoRefresh();
}

function populateSettingsForm() {
  const sbCfg = getSupabaseCfg();
  const keys = LS.get('apiKeys', {});
  const prefs = LS.get('prefs', {});
  el('settingsSbUrl').value = sbCfg.url;
  el('settingsSbKey').value = sbCfg.key;
  if (keys.claude) el('settingsClaudeKey').value = keys.claude;
  if (keys.okxKey) el('settingsOkxKey').value = keys.okxKey;
  if (keys.okxSecret) el('settingsOkxSecret').value = keys.okxSecret;
  if (keys.okxPassphrase) el('settingsOkxPassphrase').value = keys.okxPassphrase;
  if (keys.tgToken) el('settingsTgToken').value = keys.tgToken;
  if (keys.tgChatId) el('settingsTgChatId').value = keys.tgChatId;
  if (prefs.riskProfile) el('settingsRiskProfile').value = prefs.riskProfile;
  if (prefs.refreshInterval) el('settingsRefreshInterval').value = prefs.refreshInterval;
  if (prefs.tradingCapital) el('settingsTradingCapital').value = prefs.tradingCapital;
}

// ═══════════════════════════════════════════════════════════
//  OKX PUBLIC API  (no API key required)
// ═══════════════════════════════════════════════════════════
async function fetchOKXTicker(instId) {
  const url = `${CONFIG.OKX_BASE}/api/v5/market/ticker?instId=${encodeURIComponent(instId)}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const d = await res.json();
  if (d.code !== '0' || !d.data?.length) throw new Error('No data');
  const t = d.data[0];
  const last = parseFloat(t.last);
  const open = parseFloat(t.open24h);
  const change = last - open;
  return {
    price: last,
    change,
    changePercent: open ? (change / open) * 100 : 0,
    high24h: parseFloat(t.high24h),
    low24h: parseFloat(t.low24h),
    vol24h: parseFloat(t.vol24h),
    volUSDT: parseFloat(t.volCcy24h),
    source: 'OKX',
  };
}

async function fetchOKXCandles(instId, bar = CONFIG.CANDLE_BAR) {
  const url = `${CONFIG.OKX_BASE}/api/v5/market/candles?instId=${encodeURIComponent(instId)}&bar=${bar}&limit=${CONFIG.CANDLE_LIMIT}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const d = await res.json();
  if (d.code !== '0' || !d.data?.length) throw new Error('No candles');
  // OKX returns newest-first — reverse to chronological order
  return d.data.reverse().map(c => ({
    open: parseFloat(c[1]),
    high: parseFloat(c[2]),
    low: parseFloat(c[3]),
    close: parseFloat(c[4]),
    vol: parseFloat(c[5]),
  }));
}

async function fetchSymbolData(symbol) {
  try {
    const [ticker, candles1H, candles4H] = await Promise.all([
      fetchOKXTicker(symbol),
      fetchOKXCandles(symbol, '1H'),
      fetchOKXCandles(symbol, '4H'),
    ]);
    state.tickers[symbol] = ticker;
    state.indicators[symbol] = computeIndicators(candles1H, candles4H);
  } catch (err) {
    // Keep stale data if any; otherwise use demo
    if (!state.tickers[symbol]) {
      state.tickers[symbol] = mockTicker(symbol);
      state.indicators[symbol] = buildIndicatorsFromMock(symbol);
    }
    console.warn(`[${symbol}] fetch failed — using cached/demo data:`, err.message);
  }
}

async function fetchAllData() {
  const symbols = [...new Set([
    ...state.scannerSymbols,
    ...state.portfolio.map(p => p.symbol),
  ])];
  // Fetch in batches of 2 with a 250 ms gap — prevents OKX 429 rate-limit errors.
  // Each symbol fires 3 requests (ticker + 1H + 4H), so batch-of-2 = 6 concurrent.
  for (let i = 0; i < symbols.length; i += 2) {
    await Promise.allSettled(symbols.slice(i, i + 2).map(fetchSymbolData));
    if (i + 2 < symbols.length) await new Promise(r => setTimeout(r, 250));
  }
  // Fetch funding rates + open interest in small batches (non-blocking)
  (async () => {
    for (let i = 0; i < symbols.length; i += 3) {
      await Promise.allSettled(symbols.slice(i, i + 3).map(fetchOKXDerivData));
      if (i + 3 < symbols.length) await new Promise(r => setTimeout(r, 300));
    }
  })().catch(() => { });
}

async function fetchOKXDerivData(symbol) {
  // Only perpetual swap contracts have funding rates / OI
  const swapId = symbol.replace('-USDT', '-USDT-SWAP');
  try {
    const [frRes, oiRes] = await Promise.allSettled([
      fetch(`${CONFIG.OKX_BASE}/api/v5/public/funding-rate?instId=${encodeURIComponent(swapId)}`),
      fetch(`${CONFIG.OKX_BASE}/api/v5/public/open-interest?instId=${encodeURIComponent(swapId)}`),
    ]);

    const deriv = {};

    if (frRes.status === 'fulfilled' && frRes.value.ok) {
      const d = await frRes.value.json();
      if (d.code === '0' && d.data?.[0]) {
        deriv.fundingRate = parseFloat(d.data[0].fundingRate);
        deriv.nextFundingRate = parseFloat(d.data[0].nextFundingRate);
      }
    }

    if (oiRes.status === 'fulfilled' && oiRes.value.ok) {
      const d = await oiRes.value.json();
      if (d.code === '0' && d.data?.[0]) {
        deriv.openInterest = parseFloat(d.data[0].oiCcy); // OI in coin units
      }
    }

    if (Object.keys(deriv).length > 0) state.derivData[symbol] = deriv;
  } catch { }
}

// ═══════════════════════════════════════════════════════════
//  CRYPTO UTILITIES  (Web Crypto API — AES-GCM + PBKDF2)
// ═══════════════════════════════════════════════════════════
function randomHex(bytes) {
  return Array.from(crypto.getRandomValues(new Uint8Array(bytes)))
    .map(b => b.toString(16).padStart(2, '0')).join('');
}

async function hashPassword(password, salt) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(password + salt));
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
}

async function deriveKey(password, salt) {
  const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey(
    { name: 'PBKDF2', salt: new TextEncoder().encode(salt), iterations: 100_000, hash: 'SHA-256' },
    base,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt']
  );
}

async function encryptJSON(obj, password, salt) {
  const key = await deriveKey(password, salt);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const buf = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, new TextEncoder().encode(JSON.stringify(obj)));
  return {
    encrypted: btoa(String.fromCharCode(...new Uint8Array(buf))),
    iv: btoa(String.fromCharCode(...iv)),
  };
}

async function decryptJSON(encB64, ivB64, password, salt) {
  const key = await deriveKey(password, salt);
  const encrypted = Uint8Array.from(atob(encB64), c => c.charCodeAt(0));
  const iv = Uint8Array.from(atob(ivB64), c => c.charCodeAt(0));
  const buf = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, encrypted);
  return JSON.parse(new TextDecoder().decode(buf));
}

// ═══════════════════════════════════════════════════════════
//  SUPABASE  (encrypted cloud settings storage)
// ═══════════════════════════════════════════════════════════
function getSupabaseCfg() {
  const stored = LS.get('supabaseCfg', {});
  return {
    url: stored.url || CONFIG.SUPABASE_URL || '',
    key: stored.key || CONFIG.SUPABASE_ANON_KEY || '',
  };
}

function isSupabaseConfigured() {
  const { url, key } = getSupabaseCfg();
  return !!(url && key);
}

function sbHeaders(extra = {}) {
  return {
    'apikey': getSupabaseCfg().key,
    'Authorization': `Bearer ${getSupabaseCfg().key}`,
    'Content-Type': 'application/json',
    ...extra,
  };
}

async function saveToCloud(password) {
  if (!isSupabaseConfigured()) throw new Error('Supabase not configured — add URL and Anon Key in Settings.');
  const payload = {
    claude: CONFIG.CLAUDE_API_KEY, okxKey: CONFIG.OKX_API_KEY,
    okxSecret: CONFIG.OKX_SECRET_KEY, okxPassphrase: CONFIG.OKX_PASSPHRASE,
    tgToken: CONFIG.TELEGRAM_BOT_TOKEN, tgChatId: CONFIG.TELEGRAM_CHAT_ID,
    riskProfile: CONFIG.RISK_PROFILE, refreshInterval: String(CONFIG.AUTO_REFRESH_INTERVAL),
  };
  const salt = randomHex(16);
  const password_hash = await hashPassword(password, salt);
  const { encrypted, iv } = await encryptJSON(payload, password, salt);
  const res = await fetch(`${getSupabaseCfg().url}/rest/v1/app_settings`, {
    method: 'POST',
    headers: sbHeaders({ 'Prefer': 'resolution=merge-duplicates,return=minimal' }),
    body: JSON.stringify({ id: 'main', password_hash, encrypted_data: encrypted, iv, salt }),
  });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    throw new Error(e.message || `Supabase error ${res.status}`);
  }
}

async function loadFromCloud(password) {
  if (!isSupabaseConfigured()) throw new Error('Supabase not configured.');
  const res = await fetch(`${getSupabaseCfg().url}/rest/v1/app_settings?id=eq.main&select=*`, {
    headers: sbHeaders(),
  });
  if (!res.ok) throw new Error(`Cannot reach Supabase (${res.status}) — check URL and Anon Key.`);
  const rows = await res.json();
  if (!rows.length) throw new Error('No cloud data found. Fill in your settings and click "Save All to Cloud" first.');
  const row = rows[0];
  const hash = await hashPassword(password, row.salt);
  if (hash !== row.password_hash) throw new Error('Incorrect password.');
  return decryptJSON(row.encrypted_data, row.iv, password, row.salt);
}

// ═══════════════════════════════════════════════════════════
//  LOCK SCREEN
// ═══════════════════════════════════════════════════════════
function setLockError(msg) {
  const e = el('lockError');
  e.textContent = msg;
  e.style.display = msg ? 'block' : 'none';
}

async function handleUnlock() {
  const password = el('lockPasswordInput').value;
  if (!password) { setLockError('Enter your password.'); return; }

  const btn = el('lockUnlockBtn');
  btn.disabled = true;
  btn.textContent = 'Unlocking…';
  setLockError('');

  try {
    const data = await loadFromCloud(password);

    if (data.claude) CONFIG.CLAUDE_API_KEY = data.claude;
    if (data.okxKey) CONFIG.OKX_API_KEY = data.okxKey;
    if (data.okxSecret) CONFIG.OKX_SECRET_KEY = data.okxSecret;
    if (data.okxPassphrase) CONFIG.OKX_PASSPHRASE = data.okxPassphrase;
    if (data.tgToken) CONFIG.TELEGRAM_BOT_TOKEN = data.tgToken;
    if (data.tgChatId) CONFIG.TELEGRAM_CHAT_ID = data.tgChatId;
    if (data.riskProfile) CONFIG.RISK_PROFILE = data.riskProfile;
    if (data.refreshInterval) CONFIG.AUTO_REFRESH_INTERVAL = parseInt(data.refreshInterval);

    LS.set('apiKeys', {
      claude: data.claude || '', okxKey: data.okxKey || '',
      okxSecret: data.okxSecret || '', okxPassphrase: data.okxPassphrase || '',
      tgToken: data.tgToken || '', tgChatId: data.tgChatId || '',
    });
    LS.set('prefs', {
      riskProfile: data.riskProfile || 'moderate',
      refreshInterval: data.refreshInterval || '60000',
    });

    state.sessionPassword = password;
    localStorage.setItem('sp', password);
    el('lockScreen').style.display = 'none';
    toast('Unlocked — settings loaded from cloud ✓', 'success');
    await loadAppData();
  } catch (err) {
    setLockError(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Unlock';
  }
}

// ═══════════════════════════════════════════════════════════
//  TELEGRAM ALERTS
// ═══════════════════════════════════════════════════════════
async function sendTelegramAlert(message) {
  if (!CONFIG.TELEGRAM_BOT_TOKEN || !CONFIG.TELEGRAM_CHAT_ID) return;
  try {
    await fetch(`https://api.telegram.org/bot${CONFIG.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chat_id: CONFIG.TELEGRAM_CHAT_ID,
        text: message,
        parse_mode: 'HTML',
      }),
    });
  } catch { }
}

function reversalConfirmedBrowser(ind, zone) {
  // Mirrors signal_checker.py's reversal_confirmed():
  // BUY  → last candle green (close ≥ open) AND RSI turning up   (rsi ≥ rsiPrev)
  // SELL → last candle red   (close ≤ open) AND RSI turning down (rsi ≤ rsiPrev)
  // Returns true (allow alert) when there is actual evidence the move is turning.
  // Returns true also when data is insufficient — don't block on missing history.
  if (!ind || ind.lastOpen == null || ind.lastClose == null || ind.rsi == null || ind.rsiPrev == null) return true;
  if (zone === 'up') return ind.lastClose >= ind.lastOpen && ind.rsi >= ind.rsiPrev;
  if (zone === 'down') return ind.lastClose <= ind.lastOpen && ind.rsi <= ind.rsiPrev;
  return true;
}

function checkSignalAlerts() {
  const EMOJI = {
    'STRONG BUY': '🟢',
    'BUY': '🔵',
    'SELL': '🟠',
    'STRONG SELL': '🔴',
  };

  for (const sym of state.scannerSymbols) {
    const sig = state.indicators[sym]?.signal;
    const ind = state.indicators[sym];
    const price = state.tickers[sym]?.price;
    if (!sig || !price) continue;

    const isSellSignal = sig.label === 'SELL' || sig.label === 'STRONG SELL';
    const isAlert = sig.label in EMOJI;
    const holdsThisCoin = state.portfolio.some(p => p.symbol === sym);
    const shouldAlert = isAlert && (!isSellSignal || holdsThisCoin);

    // Zone-based dedup: BUY and STRONG BUY are the same zone — no repeat alert.
    // Persisted in localStorage so page refreshes never re-trigger existing signals.
    const currentZone = isSellSignal ? 'down' : isAlert ? 'up' : 'neutral';
    const lastZone = state.notifiedSignals[sym] ?? 'neutral';
    const alreadyNotified = shouldAlert && currentZone === lastZone;

    // Reversal confirmation: same filter as signal_checker.py — prevents alerting
    // into a falling knife (BUY while candle is still red and RSI still falling).
    if (shouldAlert && !alreadyNotified && !reversalConfirmedBrowser(ind, currentZone)) continue;

    if (shouldAlert && !alreadyNotified) {
      const coin = sym.replace('-USDT', '');
      const emoji = EMOJI[sig.label];
      const msg = `${emoji} <b>${sig.label}: ${coin}</b>\n`
        + `💰 Price: ${fmtCrypto(price)}\n`
        + `📊 ${sig.reasons.join('\n📊 ')}\n`
        + `⏰ ${new Date().toLocaleTimeString()}`;
      sendTelegramAlert(msg);
      state.notifiedSignals[sym] = currentZone;
      LS.set('notifiedSignals', state.notifiedSignals);
    }

    // Reset to neutral when signal clears so future zone entries can alert again
    if (!shouldAlert && state.notifiedSignals[sym] && state.notifiedSignals[sym] !== 'neutral') {
      state.notifiedSignals[sym] = 'neutral';
      LS.set('notifiedSignals', state.notifiedSignals);
    }
  }
}

// ═══════════════════════════════════════════════════════════
//  OKX PRIVATE API  (read-only — balance sync)
// ═══════════════════════════════════════════════════════════
async function hmacSHA256base64(secret, message) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']
  );
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(message));
  return btoa(String.fromCharCode(...new Uint8Array(sig)));
}

async function okxSignedGet(path) {
  const timestamp = new Date().toISOString();
  const sign = await hmacSHA256base64(CONFIG.OKX_SECRET_KEY, timestamp + 'GET' + path);
  const url = 'https://corsproxy.io/?' + encodeURIComponent(CONFIG.OKX_BASE + path);
  const res = await fetch(url, {
    headers: {
      'OK-ACCESS-KEY': CONFIG.OKX_API_KEY,
      'OK-ACCESS-SIGN': sign,
      'OK-ACCESS-TIMESTAMP': timestamp,
      'OK-ACCESS-PASSPHRASE': CONFIG.OKX_PASSPHRASE,
      'Content-Type': 'application/json',
    },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const d = await res.json();
  if (d.code !== '0') throw new Error(d.msg || `OKX error code ${d.code}`);
  return d;
}

async function okxSignedPost(path, body) {
  const timestamp = new Date().toISOString();
  const bodyStr = JSON.stringify(body);
  const sign = await hmacSHA256base64(CONFIG.OKX_SECRET_KEY, timestamp + 'POST' + path + bodyStr);
  const url = 'https://corsproxy.io/?' + encodeURIComponent(CONFIG.OKX_BASE + path);
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'OK-ACCESS-KEY': CONFIG.OKX_API_KEY,
      'OK-ACCESS-SIGN': sign,
      'OK-ACCESS-TIMESTAMP': timestamp,
      'OK-ACCESS-PASSPHRASE': CONFIG.OKX_PASSPHRASE,
      'Content-Type': 'application/json',
    },
    body: bodyStr,
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const d = await res.json();
  if (d.code !== '0') throw new Error(d.data?.[0]?.sMsg || d.msg || `OKX error ${d.code}`);
  return d;
}

async function fetchOKXBalance() {
  if (!CONFIG.OKX_API_KEY || !CONFIG.OKX_SECRET_KEY || !CONFIG.OKX_PASSPHRASE) {
    throw new Error('OKX API credentials missing — add them in Settings first.');
  }

  // Trading account (unified)
  const trading = await okxSignedGet('/api/v5/account/balance');
  const tradingDetails = trading.data[0]?.details ?? [];

  // Funding account — fetch ALL coins (not just USDT).
  // On Classic (non-Unified) OKX accounts, spot coins often live in the Funding wallet
  // and don't appear in /api/v5/account/balance at all.
  let fundingUSDT = 0;
  try {
    const funding = await okxSignedGet('/api/v5/asset/balances');
    for (const fb of (funding.data ?? [])) {
      const availBal = parseFloat(fb.availBal ?? '0') || 0;
      if (fb.ccy === 'USDT') {
        fundingUSDT = availBal;
        continue;
      }
      if (availBal <= 0) continue;
      const td = tradingDetails.find(d => d.ccy === fb.ccy);
      if (!td) {
        // Coin exists only in Funding — inject it so sync picks it up
        tradingDetails.push({ ccy: fb.ccy, availBal: fb.availBal, cashBal: fb.availBal, eq: fb.availBal });
      }
    }
  } catch (e) {
    console.warn('[OKX] Funding account fetch failed:', e.message);
  }

  // Merge USDT: use whichever account has the higher balance
  const tradingUSDT = parseFloat(tradingDetails.find(d => d.ccy === 'USDT')?.cashBal ?? '0');
  if (fundingUSDT > tradingUSDT) {
    const existing = tradingDetails.find(d => d.ccy === 'USDT');
    if (existing) existing.cashBal = String(fundingUSDT);
    else tradingDetails.push({ ccy: 'USDT', cashBal: String(fundingUSDT) });
  }

  return tradingDetails;
}

function showUsdtBalance(amount) {
  state.usdtBalance = amount;
  el('usdtBalance').textContent = '$' + amount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  el('usdtBalanceItem').style.display = '';
}

async function syncPortfolioFromOKX() {
  const btn = el('okxSyncBtn');
  try {
    btn.disabled = true;
    btn.textContent = 'Syncing…';
    const details = await fetchOKXBalance();
    let added = 0, updated = 0, usdtBal = 0;

    // Parse a coin balance from OKX account details.
    // Prefer availBal (what OKX app shows) when positive; fall back to cashBal (total
    // including frozen-in-orders) when availBal is missing or zero. This handles the case
    // where all coins are locked in an open order — the coin still belongs to the user.
    // NOTE: never use JS `||` here — `"0" || fallback` returns `"0"` (string "0" is truthy).
    function coinBal(d) {
      // Use availBal — what OKX's own UI shows (coins free to trade).
      // cashBal includes coins frozen in active algo/limit orders, which would inflate
      // the portfolio display and show locked coins as open positions.
      const avail = parseFloat(d.availBal ?? 'NaN');
      if (avail > 0) return avail;
      // Fall back to cashBal only if availBal is genuinely missing
      const cash = parseFloat(d.cashBal ?? 'NaN');
      if (cash > 0) return cash;
      return parseFloat(d.eq ?? '0') || 0;
    }

    // Build a ccy→balance map for stale-entry cleanup.
    // Use coinBal (same logic as the add loop) so a coin isn't flagged as zero
    // just because cashBal is "0" while availBal is non-zero (or vice versa).
    const okxTotalMap = {};
    for (const d of details) {
      okxTotalMap[d.ccy] = coinBal(d);
    }

    for (const d of details) {
      if (d.ccy === 'USDT') {
        usdtBal = parseFloat(d.cashBal ?? d.eq ?? '0');
        state.usdtBalance = usdtBal;
        continue;
      }

      const bal = coinBal(d);
      if (bal <= 0) continue;

      const symbol = d.ccy + '-USDT';

      if (!state.tickers[symbol]) await fetchSymbolData(symbol);
      const price = state.tickers[symbol]?.price ?? 0;
      if (price > 0 && bal * price < 1) continue;

      // OKX provides two avg-price fields:
      //   accAvgPx  — accumulated avg cost including fees (matches OKX UI "Spot PnL")
      //   openAvgPx — raw open avg price (no fees included)
      // Try accAvgPx first; it should produce the same P&L as the OKX portfolio page.
      const accAvgPx = parseFloat(d.accAvgPx ?? '0') || 0;
      const openAvgPx = parseFloat(d.openAvgPx ?? '0') || 0;
      const apiAvgPx = accAvgPx > 0 ? accAvgPx : openAvgPx;
      // Never fall back to current price — that would show $0 P&L which is wrong.
      // If OKX returns no avg price, leave avgBuyPrice as 0 (P&L displays '—').

      const existing = state.portfolio.find(p => p.symbol === symbol);
      if (existing) {
        existing.amount = bal;
        if (apiAvgPx > 0) existing.avgBuyPrice = apiAvgPx;
        updated++;
      } else {
        state.portfolio.push({ symbol, amount: bal, avgBuyPrice: apiAvgPx });
        added++;
      }

      if (!state.scannerSymbols.includes(symbol)) {
        state.scannerSymbols.push(symbol);
        saveScannerSymbols();
      }
    }

    // Remove stale entries: coins whose total OKX balance is now zero or dust.
    // Uses cashBal (total including frozen) so a coin locked in an open order is kept.
    state.portfolio = state.portfolio.filter(p => {
      const ccy = p.symbol.replace('-USDT', '');
      if (!(ccy in okxTotalMap)) return true; // not in OKX response — keep (manual entry)
      const total = okxTotalMap[ccy];
      const price = state.tickers[p.symbol]?.price ?? 0;
      if (total <= 0 || (price > 0 && total * price < 1)) return false; // truly zero/dust — remove
      return true;
    });

    LS.set('portfolio', state.portfolio);
    LS.set('portfolioUpdated', Date.now());
    await pushPortfolioSymbols();   // symbols list for signal checker
    await pushFullPortfolio();      // full data for cross-device sync

    if (usdtBal > 0) {
      showUsdtBalance(usdtBal);
      LS.set('lastUsdtBalance', usdtBal);
    }

    await fetchAllData();
    renderPortfolio();
    renderScanner();

    toast(`OKX synced: ${added} new, ${updated} updated`, 'success');
  } catch (err) {
    toast('OKX sync failed: ' + err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '⟳ Sync OKX';
  }
}

// ═══════════════════════════════════════════════════════════
//  DEMO / FALLBACK DATA
// ═══════════════════════════════════════════════════════════
const DEMO_BASE_PRICES = {
  'BTC-USDT': 67500, 'ETH-USDT': 3420, 'SOL-USDT': 175, 'BNB-USDT': 615,
  'XRP-USDT': 0.62, 'DOGE-USDT': 0.165, 'ADA-USDT': 0.48, 'AVAX-USDT': 38,
  'MATIC-USDT': 0.72, 'DOT-USDT': 8.4,
};

function mockTicker(symbol) {
  const seed = symbol.split('').reduce((a, c) => a + c.charCodeAt(0), 0);
  const base = DEMO_BASE_PRICES[symbol] || ((seed % 500) + 1);
  const chgPct = ((seed % 21) - 10) * 0.4;
  return {
    price: base, change: base * chgPct / 100, changePercent: chgPct,
    high24h: base * 1.05, low24h: base * 0.95,
    vol24h: (seed % 10 + 1) * 10000, volUSDT: base * 100000,
    source: 'Demo',
  };
}

function buildIndicatorsFromMock(symbol) {
  const seed = symbol.split('').reduce((a, c) => a + c.charCodeAt(0), 0);
  const rsi = 20 + (seed % 60);
  const macdBull = (seed % 3) === 0;
  const bbPct = (seed % 100) / 100;
  const mockMacd = { trend: macdBull ? 'bullish' : 'bearish', bullishCross: macdBull && (seed % 5 === 0), bearishCross: !macdBull && (seed % 5 === 0), macd: macdBull ? 0.002 : -0.002, signal: 0 };
  const mockBB = { pctB: bbPct, upper: 1, middle: 0.97, lower: 0.94 };
  return { rsi, rsi4h: null, macd: mockMacd, bb: mockBB, volRatio: null, signal: generateSignal(rsi, mockMacd, mockBB), source: 'Demo' };
}

// ═══════════════════════════════════════════════════════════
//  TECHNICAL INDICATORS
// ═══════════════════════════════════════════════════════════

function calcRSI(closes, period = 14) {
  if (closes.length < period + 1) return null;
  let avgGain = 0, avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const d = closes[i] - closes[i - 1];
    if (d > 0) avgGain += d; else avgLoss -= d;
  }
  avgGain /= period;
  avgLoss /= period;
  for (let i = period + 1; i < closes.length; i++) {
    const d = closes[i] - closes[i - 1];
    avgGain = (avgGain * (period - 1) + Math.max(d, 0)) / period;
    avgLoss = (avgLoss * (period - 1) + Math.max(-d, 0)) / period;
  }
  if (avgLoss === 0) return 100;
  return 100 - (100 / (1 + avgGain / avgLoss));
}

function emaArray(vals, period) {
  const k = 2 / (period + 1);
  const out = [vals[0]];
  for (let i = 1; i < vals.length; i++) out.push(vals[i] * k + out[i - 1] * (1 - k));
  return out;
}

function calcMACD(closes) {
  if (closes.length < 35) return null;
  const ema12 = emaArray(closes, 12);
  const ema26 = emaArray(closes, 26);
  const macdLine = ema12.map((v, i) => v - ema26[i]).slice(25);
  const sigLine = emaArray(macdLine, 9);
  const n = macdLine.length - 1;
  return {
    macd: macdLine[n],
    signal: sigLine[n],
    histogram: macdLine[n] - sigLine[n],
    trend: macdLine[n] > sigLine[n] ? 'bullish' : 'bearish',
    bullishCross: n > 0 && macdLine[n - 1] < sigLine[n - 1] && macdLine[n] >= sigLine[n],
    bearishCross: n > 0 && macdLine[n - 1] > sigLine[n - 1] && macdLine[n] <= sigLine[n],
  };
}

function calcBB(closes, period = 20) {
  if (closes.length < period) return null;
  const slice = closes.slice(-period);
  const mean = slice.reduce((a, b) => a + b, 0) / period;
  const std = Math.sqrt(slice.reduce((a, b) => a + (b - mean) ** 2, 0) / period);
  const upper = mean + 2 * std;
  const lower = mean - 2 * std;
  const price = closes[closes.length - 1];
  return { upper, middle: mean, lower, pctB: upper > lower ? (price - lower) / (upper - lower) : 0.5 };
}

function computeIndicators(candles1H, candles4H = []) {
  const closes = candles1H.map(c => c.close);
  const opens = candles1H.map(c => c.open);
  const volumes = candles1H.map(c => c.vol);

  const rsi = calcRSI(closes);
  const rsiPrev = closes.length > 1 ? calcRSI(closes.slice(0, -1)) : null;
  const macd = calcMACD(closes);
  const bb = calcBB(closes);

  // 4H RSI — higher-timeframe trend confirmation
  const closes4H = candles4H.map(c => c.close);
  const rsi4h = closes4H.length > 14 ? calcRSI(closes4H) : null;

  // Volume ratio: latest candle vs 20-bar average
  let volRatio = null;
  if (volumes.length >= 21) {
    const avg = volumes.slice(-21, -1).reduce((a, b) => a + b, 0) / 20;
    if (avg > 0) volRatio = volumes[volumes.length - 1] / avg;
  }

  // Last candle direction + RSI momentum — used by reversal confirmation filter
  const lastOpen = opens.at(-1) ?? null;
  const lastClose = closes.at(-1) ?? null;

  return { rsi, rsiPrev, rsi4h, macd, bb, volRatio, lastOpen, lastClose, signal: generateSignal(rsi, macd, bb, rsi4h, volRatio) };
}

// ═══════════════════════════════════════════════════════════
//  SIGNAL ENGINE
//  Score: positive = buy pressure, negative = sell pressure
//  RSI: ±3 max, MACD: ±2, BB: ±2 → total range -7 to +7
// ═══════════════════════════════════════════════════════════
function generateSignal(rsi, macd, bb, rsi4h = null, volRatio = null) {
  let score = 0;
  const reasons = [];

  // ── RSI ──
  if (rsi !== null) {
    if (rsi <= 20) { score += 3; reasons.push(`RSI ${rsi.toFixed(0)} — extremely oversold`); }
    else if (rsi <= 30) { score += 2; reasons.push(`RSI ${rsi.toFixed(0)} — oversold`); }
    else if (rsi <= 40) { score += 1; reasons.push(`RSI ${rsi.toFixed(0)} — below neutral`); }
    else if (rsi >= 80) { score -= 3; reasons.push(`RSI ${rsi.toFixed(0)} — extremely overbought`); }
    else if (rsi >= 70) { score -= 2; reasons.push(`RSI ${rsi.toFixed(0)} — overbought`); }
    else if (rsi >= 60) { score -= 1; reasons.push(`RSI ${rsi.toFixed(0)} — above neutral`); }
  }

  // ── MACD ──
  if (macd !== null) {
    if (macd.bullishCross) { score += 2; reasons.push('MACD bullish crossover'); }
    else if (macd.bearishCross) { score -= 2; reasons.push('MACD bearish crossover'); }
    else if (macd.trend === 'bullish') score += 0.5;
    else score -= 0.5;
  }

  // ── Bollinger Bands ──
  if (bb !== null) {
    if (bb.pctB <= 0.05) { score += 2; reasons.push('Price at lower Bollinger Band'); }
    else if (bb.pctB <= 0.20) { score += 1; reasons.push('Price near lower BB'); }
    else if (bb.pctB >= 0.95) { score -= 2; reasons.push('Price at upper Bollinger Band'); }
    else if (bb.pctB >= 0.80) { score -= 1; reasons.push('Price near upper BB'); }
  }

  // ── 4H RSI confirmation (max ±1) ──
  if (rsi4h !== null) {
    if (score > 0 && rsi4h <= 45) { score += 1; reasons.push(`4H RSI ${rsi4h.toFixed(0)} — higher-TF uptrend confirmed`); }
    else if (score < 0 && rsi4h >= 55) { score -= 1; reasons.push(`4H RSI ${rsi4h.toFixed(0)} — higher-TF downtrend confirmed`); }
    else if (score > 0 && rsi4h >= 70) { score -= 0.5; reasons.push(`4H RSI ${rsi4h.toFixed(0)} — caution: overbought on 4H`); }
    else if (score < 0 && rsi4h <= 30) { score += 0.5; reasons.push(`4H RSI ${rsi4h.toFixed(0)} — caution: oversold on 4H`); }
  }

  // ── Volume spike confirmation (max ±1) ──
  if (volRatio !== null && volRatio >= 1.5) {
    if (score >= 2) { score += 1; reasons.push(`Volume ${volRatio.toFixed(1)}× avg — strong buying interest`); }
    else if (score <= -2) { score -= 1; reasons.push(`Volume ${volRatio.toFixed(1)}× avg — strong selling pressure`); }
    else if (volRatio >= 2) reasons.push(`Volume spike ${volRatio.toFixed(1)}× avg (no strong signal yet)`);
  }

  let label, cls;
  if (score >= 4) { label = 'STRONG BUY'; cls = 'sig-sbuy'; }
  else if (score >= 2) { label = 'BUY'; cls = 'sig-buy'; }
  else if (score > -2) { label = 'HOLD'; cls = 'sig-hold'; }
  else if (score > -4) { label = 'SELL'; cls = 'sig-sell'; }
  else { label = 'STRONG SELL'; cls = 'sig-ssell'; }

  return { score, label, cls, reasons };
}

// ═══════════════════════════════════════════════════════════
//  RENDER — SCANNER
// ═══════════════════════════════════════════════════════════
function renderScanner() {
  const tbody = el('scannerBody');
  let symbols = [...state.scannerSymbols];

  // Filter
  if (state.scannerFilter === 'buy') symbols = symbols.filter(s => (state.indicators[s]?.signal.score ?? 0) >= 2);
  if (state.scannerFilter === 'sell') symbols = symbols.filter(s => (state.indicators[s]?.signal.score ?? 0) <= -2);
  if (state.scannerFilter === 'hold') symbols = symbols.filter(s => { const sc = state.indicators[s]?.signal.score ?? 0; return sc > -2 && sc < 2; });

  // Sort
  symbols.sort((a, b) => {
    if (state.scannerSort === 'signal') return (state.indicators[b]?.signal.score ?? 0) - (state.indicators[a]?.signal.score ?? 0);
    if (state.scannerSort === 'change') return (state.tickers[b]?.changePercent ?? 0) - (state.tickers[a]?.changePercent ?? 0);
    if (state.scannerSort === 'price') return (state.tickers[b]?.price ?? 0) - (state.tickers[a]?.price ?? 0);
    return a.localeCompare(b);
  });

  if (!symbols.length) {
    tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--text-muted);padding:28px">No results for this filter.</td></tr>';
    updateBestPickBanner();
    return;
  }

  tbody.innerHTML = symbols.map(sym => {
    const t = state.tickers[sym];
    const ind = state.indicators[sym];
    const sig = ind?.signal;

    const price = t?.price ?? 0;
    const chgPct = t?.changePercent ?? 0;
    const rsi = ind?.rsi ?? null;
    const rsi4h = ind?.rsi4h ?? null;
    const macd = ind?.macd ?? null;
    const bb = ind?.bb ?? null;
    const volRatio = ind?.volRatio ?? null;
    const score = sig?.score ?? 0;
    const isDemo = t?.source === 'Demo';

    const chgCls = chgPct >= 0 ? 'pos' : 'neg';

    // 1H RSI badge
    const rsiCls = rsi === null ? '' : rsi <= 30 ? 'rsi-low' : rsi >= 70 ? 'rsi-high' : 'rsi-mid';

    // 4H RSI badge
    const rsi4hCls = rsi4h === null ? '' : rsi4h <= 35 ? 'rsi-low' : rsi4h >= 65 ? 'rsi-high' : 'rsi-mid';
    const rsi4hTxt = rsi4h !== null ? rsi4h.toFixed(0) : '—';

    // MACD
    const macdTxt = macd
      ? (macd.bullishCross ? '↑ Cross' : macd.bearishCross ? '↓ Cross'
        : macd.trend === 'bullish' ? '↑ Bull' : '↓ Bear')
      : '—';
    const macdCls = macd ? (macd.trend === 'bullish' || macd.bullishCross ? 'pos' : 'neg') : '';

    // BB%
    const bbPct = bb ? (bb.pctB * 100).toFixed(0) + '%' : '—';
    const bbCls = bb ? (bb.pctB <= 0.2 ? 'pos' : bb.pctB >= 0.8 ? 'neg' : '') : '';

    // Volume
    const volTxt = volRatio !== null ? volRatio.toFixed(1) + '×' : '—';
    const volCls = volRatio === null ? '' : volRatio >= 2 ? 'vol-spike' : volRatio >= 1.5 ? 'vol-high' : 'vol-normal';

    // Score chip inside signal badge
    const scoreStr = (score >= 0 ? '+' : '') + score.toFixed(1);

    const inPort = state.portfolio.some(p => p.symbol === sym);
    const reason = sig?.reasons?.join(' · ') || 'Neutral — no strong signal';
    const coin = sym.replace('-USDT', '').replace('-BTC', '');
    const tooltip = escHtml(`Score: ${scoreStr} | ${reason}`);

    return `<tr class="scanner-row ${sig?.cls ?? ''}">
      <td>
        <div class="sym-cell">
          <span class="coin-icon">${coin}</span>
          ${inPort ? '<span class="tag-hold" style="font-size:9px;margin-left:3px">HELD</span>' : ''}
          ${isDemo ? '<span class="demo-tag">DEMO</span>' : ''}
        </div>
      </td>
      <td class="num price-cell">${price ? fmtCrypto(price) : '—'}</td>
      <td class="num ${chgCls}">${price ? (chgPct >= 0 ? '+' : '') + chgPct.toFixed(2) + '%' : '—'}</td>
      <td class="num"><span class="rsi-badge ${rsiCls}">${rsi !== null ? rsi.toFixed(0) : '—'}</span></td>
      <td class="num"><span class="rsi-badge ${rsi4hCls}">${rsi4hTxt}</span></td>
      <td class="num ${macdCls}">${macdTxt}</td>
      <td class="num ${bbCls}">${bbPct}</td>
      <td class="num ${volCls}">${volTxt}</td>
      <td>
        <span class="signal-badge ${sig?.cls ?? 'sig-hold'}" title="${tooltip}">
          ${sig?.label ?? '—'} <span class="score-chip">${scoreStr}</span>
        </span>
      </td>
      <td><button class="btn-row-del" data-sym="${sym}" title="Remove from scanner">✕</button></td>
    </tr>`;
  }).join('');

  tbody.querySelectorAll('.btn-row-del').forEach(btn => {
    btn.addEventListener('click', () => removeScannerSymbol(btn.dataset.sym));
  });

  updateBestPickBanner();
}

function updateBestPickBanner() {
  const banner = el('bestPickBar');
  let best = null, bestScore = -99;
  for (const sym of state.scannerSymbols) {
    const sig = state.indicators[sym]?.signal;
    if (sig && sig.score > bestScore) { bestScore = sig.score; best = { sym, sig }; }
  }
  if (!best || best.sig.score < 2) { banner.style.display = 'none'; return; }
  const coin = best.sym.replace('-USDT', '');
  const t = state.tickers[best.sym];
  banner.style.display = 'flex';
  banner.innerHTML = `
    <span class="bp-label">Top Pick</span>
    <span class="coin-icon" style="background:rgba(34,197,94,.15)">${coin}</span>
    <span class="signal-badge ${best.sig.cls}">${best.sig.label}</span>
    <span class="bp-reason">${escHtml(best.sig.reasons.join(' · '))}</span>
    ${t ? `<span class="bp-price">${fmtCrypto(t.price)}</span>` : ''}
  `;
}

function removeScannerSymbol(sym) {
  state.scannerSymbols = state.scannerSymbols.filter(s => s !== sym);
  saveScannerSymbols();
  renderScanner();
  toast(`Removed ${sym}`, 'info');
}

// ═══════════════════════════════════════════════════════════
//  RENDER — PORTFOLIO
// ═══════════════════════════════════════════════════════════
function renderPortfolio() {
  const tbody = el('portfolioBody');

  if (!state.portfolio.length) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--text-muted);padding:28px;font-size:12px">
      No positions yet. Click <strong>+ Add Position</strong> to track your OKX holdings.
    </td></tr>`;
    updateSummaryBar();
    return;
  }

  let totalValue = 0, totalCost = 0, totalDayPnl = 0;

  const rows = state.portfolio.map(pos => {
    const t = state.tickers[pos.symbol];
    const price = t?.price ?? 0;
    const chgPct = t?.changePercent ?? 0;
    const value = price * pos.amount;
    const cost = pos.avgBuyPrice * pos.amount;
    const pnl = value - cost;
    const pnlPct = cost ? (pnl / cost) * 100 : 0;
    const dayPnl = t ? t.change * pos.amount : 0;

    totalValue += value;
    totalCost += cost;
    totalDayPnl += dayPnl;

    const chgCls = chgPct >= 0 ? 'pos' : 'neg';
    const pnlCls = pnl >= 0 ? 'pos' : 'neg';
    const coin = pos.symbol.replace('-USDT', '');

    return `<tr>
      <td><div class="sym-cell"><span class="coin-icon">${coin}</span></div></td>
      <td class="num price-cell">${price ? fmtCrypto(price) : '—'}</td>
      <td class="num ${chgCls}">${price ? (chgPct >= 0 ? '+' : '') + chgPct.toFixed(2) + '%' : '—'}</td>
      <td class="num">${pos.amount}</td>
      <td class="num price-cell">
        ${price ? fmtMoney(value) : '—'}
        ${cost > 0 ? `<div class="invested-sub">invested ${fmtMoney(cost)}</div>` : ''}
      </td>
      <td class="num ${pnlCls} pnl-cell">
        ${price ? `<span class="pnl-dollar">${pnl >= 0 ? '+' : ''}${fmtMoney(pnl)}</span>` : '—'}
        ${price && cost > 0 ? `<div class="pnl-pct-sub">${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%</div>` : ''}
      </td>
      <td><button class="btn-row-del" data-symbol="${pos.symbol}" title="Remove">✕</button></td>
    </tr>`;
  }).join('');

  tbody.innerHTML = rows;
  tbody.querySelectorAll('.btn-row-del').forEach(btn => {
    btn.addEventListener('click', () => removePosition(btn.dataset.symbol));
  });

  updateSummaryBar(totalValue, totalCost, totalDayPnl);
}

function updateSummaryBar(totalValue = 0, totalCost = 0, totalDayPnl = 0) {
  const totalPnl = totalValue - totalCost;
  const totalPnlPct = totalCost ? (totalPnl / totalCost) * 100 : 0;

  el('totalPortfolioValue').textContent = totalValue ? fmtMoney(totalValue) : '—';

  const dayEl = el('totalDayPnl');
  dayEl.textContent = totalDayPnl ? (totalDayPnl >= 0 ? '+' : '') + fmtMoney(totalDayPnl) : '—';
  dayEl.className = 'summary-value ' + (totalDayPnl >= 0 ? 'pos' : 'neg');

  const pnlEl = el('totalPnl');
  pnlEl.textContent = totalPnl ? `${totalPnl >= 0 ? '+' : ''}${fmtMoney(totalPnl)} (${totalPnlPct >= 0 ? '+' : ''}${totalPnlPct.toFixed(2)}%)` : '—';
  pnlEl.className = 'summary-value ' + (totalPnl >= 0 ? 'pos' : 'neg');

  el('lastUpdated').textContent = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });

  // Best signal in the top bar
  const topEl = el('topSignal');
  let best = null, bestScore = -99;
  for (const sym of state.scannerSymbols) {
    const sig = state.indicators[sym]?.signal;
    if (sig && sig.score > bestScore) { bestScore = sig.score; best = { sym, sig }; }
  }
  if (best && best.sig.score >= 2) {
    const coin = best.sym.replace('-USDT', '');
    topEl.textContent = `${coin} — ${best.sig.label}`;
    topEl.className = 'summary-value pos';
  } else if (best) {
    topEl.textContent = 'No strong signal';
    topEl.className = 'summary-value';
  }
}

function removePosition(symbol) {
  state.portfolio = state.portfolio.filter(p => p.symbol !== symbol);
  savePortfolio();
  renderPortfolio();
  toast(`Removed ${symbol}`, 'info');
}

// ═══════════════════════════════════════════════════════════
//  RENDER — NEWS
// ═══════════════════════════════════════════════════════════
async function fetchWithTimeout(url, ms = 7000) {
  const ctrl = new AbortController();
  const id = setTimeout(() => ctrl.abort(), ms);
  try { return await fetch(url, { signal: ctrl.signal }); }
  finally { clearTimeout(id); }
}

async function fetchNews(topic = '') {
  // RSS via corsproxy.io — free, no API key, CORS-safe.
  // CryptoPanic aggregates Twitter/X, Reddit, and all major outlets in one feed —
  // it's the closest free substitute for X since Twitter killed its free API in 2023.
  // proxies: per-feed override — use when a site blocks corsproxy.io specifically.
  const PROXY_CORSPROXY = u => `https://corsproxy.io/?${encodeURIComponent(u)}`;
  const PROXY_ALLORIGINS = u => `https://api.allorigins.win/raw?url=${encodeURIComponent(u)}`;

  const feeds = [
    // CryptoPanic blocks corsproxy.io → use allorigins only (avoids the 403 console error)
    { rss: 'https://cryptopanic.com/news/rss/', source: 'CryptoPanic', proxies: [PROXY_ALLORIGINS] },
    { rss: 'https://cointelegraph.com/rss', source: 'CoinTelegraph', proxies: [PROXY_CORSPROXY, PROXY_ALLORIGINS] },
    { rss: 'https://decrypt.co/feed', source: 'Decrypt', proxies: [PROXY_CORSPROXY, PROXY_ALLORIGINS] },
    { rss: 'https://www.coindesk.com/arc/outboundfeeds/rss/', source: 'CoinDesk', proxies: [PROXY_CORSPROXY, PROXY_ALLORIGINS] },
    { rss: 'https://bitcoinmagazine.com/.rss/full/', source: 'Bitcoin Magazine', proxies: [PROXY_CORSPROXY, PROXY_ALLORIGINS] },
    { rss: 'https://www.theblock.co/rss.xml', source: 'The Block', proxies: [PROXY_CORSPROXY, PROXY_ALLORIGINS] },
  ];

  for (const feed of feeds) {
    try {
      let text = null;
      for (const proxy of feed.proxies) {
        try {
          const res = await fetchWithTimeout(proxy(feed.rss));
          if (!res.ok) continue;
          text = await res.text();
          if (text) break;
        } catch { }
      }
      if (!text) continue;

      const xml = new DOMParser().parseFromString(text, 'text/xml');
      const items = Array.from(xml.querySelectorAll('item'));
      if (!items.length) continue;

      const getText = (node, tag) => node.querySelector(tag)?.textContent?.trim() || '';

      let articles = items.map(item => ({
        title: getText(item, 'title'),
        link: getText(item, 'link'),
        description: getText(item, 'description').replace(/<[^>]*>/g, ''),
        pubDate: getText(item, 'pubDate'),
      })).filter(a => a.title);

      if (topic) {
        const t = topic.toLowerCase();
        articles = articles.filter(a => (a.title + ' ' + a.description).toLowerCase().includes(t));
      }
      if (!articles.length) continue;

      state.news = articles.slice(0, CONFIG.MAX_NEWS_ARTICLES).map(a => ({
        title: a.title,
        summary: a.description.substring(0, 160) + (a.description.length > 160 ? '…' : ''),
        source: feed.source,
        url: a.link,
        age: a.pubDate ? timeAgo(new Date(a.pubDate)) : 'recently',
        sentiment: guessSentiment(a.title + ' ' + a.description),
      }));
      return;
    } catch { }
  }

  state.news = getDemoNews();
}

function guessSentiment(text) {
  const t = text.toLowerCase();
  const pos = /\b(surge|rally|gain|bull|beat|growth|profit|rise|jump|soar|record|optimis|strong|upgrade|breakout|accumulate|adoption|ath|all.time.high|partnership|launch)\b/.test(t);
  const neg = /\b(drop|fall|crash|bear|miss|loss|decline|plunge|slump|warning|risk|down|weak|downgrade|recession|hack|exploit|ban|regulation|fear|liquidat|sell.off|collapse)\b/.test(t);
  if (pos && !neg) return 'pos';
  if (neg && !pos) return 'neg';
  return 'neu';
}

function getDemoNews() {
  return [
    { title: 'Bitcoin breaks key resistance, analysts target $75K next', summary: 'BTC surged past $68K as institutional buying accelerated following positive macro data from the US.', source: 'CoinDesk', url: '#', age: '15m ago', sentiment: 'pos' },
    { title: 'Ethereum ETF inflows hit record $2.1B this week', summary: 'Spot ETH ETFs saw massive capital inflows as demand from traditional finance continues to grow rapidly.', source: 'TheBlock', url: '#', age: '1h ago', sentiment: 'pos' },
    { title: 'Federal Reserve signals rate cuts boosting crypto assets', summary: 'Risk assets including crypto rallied after Fed minutes showed growing confidence inflation is cooling.', source: 'Reuters', url: '#', age: '3h ago', sentiment: 'pos' },
    { title: 'SEC scrutiny of crypto exchanges continues into Q4', summary: 'Regulatory uncertainty persists as the SEC broadens its examination of spot crypto trading venues.', source: 'Bloomberg', url: '#', age: '5h ago', sentiment: 'neg' },
    { title: 'Solana ecosystem TVL hits new all-time high of $12B', summary: 'The Solana DeFi ecosystem continues to attract capital, with total value locked surpassing previous records.', source: 'CoinTelegraph', url: '#', age: '7h ago', sentiment: 'pos' },
    { title: 'DeFi total value locked surpasses $100B milestone', summary: 'The decentralized finance ecosystem hits a key psychological milestone as crypto market confidence grows.', source: 'DeFiLlama', url: '#', age: '9h ago', sentiment: 'pos' },
    { title: 'DOGE and meme coins lag as blue-chip crypto leads rally', summary: 'While BTC and ETH gained 5%+ this week, speculative altcoins saw reduced retail interest overall.', source: 'MarketWatch', url: '#', age: '11h ago', sentiment: 'neu' },
    { title: 'OKX trading volume surges 40% amid increased retail activity', summary: 'The exchange reports record spot trading volume as new retail participants enter the crypto market.', source: 'OKX Blog', url: '#', age: '14h ago', sentiment: 'pos' },
  ];
}

function calcNewsSentiment() {
  if (!state.news.length) return 50;
  const scores = state.news.map(n => n.sentiment === 'pos' ? 1 : n.sentiment === 'neg' ? 0 : 0.5);
  return Math.round(scores.reduce((a, b) => a + b, 0) / scores.length * 100);
}

function renderNews() {
  const list = el('newsList');
  if (!state.news.length) {
    list.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:32px;font-size:12px">No news loaded.</div>';
    return;
  }
  list.innerHTML = state.news.map(n => `
    <a class="news-item" href="${n.url}" target="_blank" rel="noopener">
      <div class="news-meta">
        <span class="news-source">${escHtml(n.source)}</span>
        <span class="news-age">${escHtml(n.age)}</span>
        <span class="news-sentiment-badge ${n.sentiment}">${n.sentiment === 'pos' ? 'Bullish' : n.sentiment === 'neg' ? 'Bearish' : 'Neutral'}</span>
      </div>
      <div class="news-title">${escHtml(n.title)}</div>
      ${n.summary ? `<div class="news-summary">${escHtml(n.summary)}</div>` : ''}
    </a>
  `).join('');

  const score = calcNewsSentiment();
  el('sentimentFill').style.width = score + '%';
  el('sentimentFill').style.background = score >= 60 ? 'var(--green)' : score <= 40 ? 'var(--red)' : 'var(--amber)';
  el('sentimentScore').textContent = score + '% bullish';
  el('sentimentScore').style.color = score >= 60 ? 'var(--green)' : score <= 40 ? 'var(--red)' : 'var(--amber)';
}

// ═══════════════════════════════════════════════════════════
//  AI ADVISOR  (Claude API, called directly from browser)
// ═══════════════════════════════════════════════════════════
async function runAiAnalysis() {
  const contextType = el('aiContextSelect').value;
  const customText = el('aiCustomInput').value.trim();

  if (contextType === 'custom' && !customText) {
    toast('Enter a question for the AI', 'error'); return;
  }
  if (!CONFIG.CLAUDE_API_KEY) {
    renderAiError('Add your Claude API key in Settings (⚙ icon) to enable AI analysis. Get a free key at console.anthropic.com');
    return;
  }

  showAiThinking();

  try {
    const res = await fetch(CONFIG.CLAUDE_API_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': CONFIG.CLAUDE_API_KEY,
        'anthropic-version': '2023-06-01',
        'anthropic-dangerous-direct-browser-access': 'true',
      },
      body: JSON.stringify({
        model: CONFIG.CLAUDE_MODEL,
        max_tokens: 1800,
        system: buildSystemPrompt(),
        messages: [{ role: 'user', content: buildPrompt(contextType, customText) }],
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error?.message || `HTTP ${res.status}`);
    }

    const data = await res.json();
    renderAiResponse(data.content?.[0]?.text ?? '(No response)');
  } catch (err) {
    renderAiError('API error: ' + err.message);
  }
}

function buildSystemPrompt() {
  const availableCapital = state.usdtBalance > 0 ? state.usdtBalance : CONFIG.TRADING_CAPITAL;
  const capital = availableCapital > 0
    ? `Available USDT to trade: $${availableCapital.toLocaleString('en-US', { maximumFractionDigits: 2 })}${state.usdtBalance > 0 ? ' (live from OKX)' : ' (manually set)'}`
    : 'Available USDT: unknown — user has not synced OKX balance or set trading capital';
  const riskPct = CONFIG.RISK_PROFILE === 'conservative' ? 10 : CONFIG.RISK_PROFILE === 'aggressive' ? 30 : 20;
  const positionSize = availableCapital > 0
    ? `Suggested position size per trade: $${(availableCapital * riskPct / 100).toFixed(2)} USDT (${riskPct}% of capital for ${CONFIG.RISK_PROFILE} risk — never exceed 30%)`
    : 'Position size: suggest a % of capital since exact amount is unknown';
  const ownedCoins = state.portfolio.length
    ? state.portfolio.map(p => p.symbol).join(', ')
    : 'none';

  return `You are an expert cryptocurrency trading advisor specializing in technical analysis for OKX spot markets. The user is a retail trader who wants clear, actionable guidance.

Rules:
- Be direct and concise. Use ### headings and bullet points.
- Always tag recommendations: [BUY], [SELL], or [HOLD]
- For every BUY or SELL, give:
  • Entry price (exact or range)
  • How much USDT to spend (based on their capital and risk profile)
  • How many coins that buys at entry price
  • Take Profit target (+%)
  • Stop Loss (-%)
  • Confidence level: High / Medium / Low
- Risk profile: ${CONFIG.RISK_PROFILE}
- ${capital}
- ${positionSize}
- Platform: OKX Spot Trading (not futures/leverage)
- Coins user currently holds: ${ownedCoins}
- IMPORTANT: Only recommend [SELL] for coins the user currently holds (listed above). Never suggest selling a coin they don't own.

TRADE TAGS — REQUIRED for every actionable [BUY] or [SELL]:
Use the OPTION 3 format below for ALL buy trades — it maximizes profit with partial take-profit + trailing stop.
After each concrete BUY recommendation, append this tag on its own line (valid JSON, numbers only — no $ signs, no quotes around numbers):

[TRADE:{"side":"buy","symbol":"AVAX-USDT","amountUsdt":70,"partialTpPct":5,"trailingCallbackPct":3,"slPct":8}]

  • side: "buy" or "sell"
  • symbol: exact OKX instrument ID (e.g. "AVAX-USDT", "SOL-USDT")
  • amountUsdt: USDT to spend
  • partialTpPct: % gain at which to sell 50% and lock in profit (Phase 1)
  • trailingCallbackPct: trailing stop callback % for remaining 50% after Phase 1 triggers (Phase 2)
  • slPct: initial stop loss % below entry (protects full position before Phase 1 triggers)

OPTION 3 GUIDELINES — choose values based on coin volatility tier:

Extreme volatility (PEPE, WIF, DOGE): partialTpPct 6-8, trailingCallbackPct 3-4, slPct 8-10
High volatility (AVAX, SOL, SUI, INJ, TIA): partialTpPct 4-6, trailingCallbackPct 2.5-3, slPct 7-8
Medium-high (NEAR, APT, FET, LINK, SEI): partialTpPct 3-5, trailingCallbackPct 2-2.5, slPct 6-7

Signal strength modifiers (adjust partialTpPct upward):
  • score ≥ 4.5: +1–2% (strong conviction — let winners run more)
  • RSI 1H < 25 (deeply oversold): +1%
  • RSI 1H 30–40 (mild dip): −0.5% (more conservative)
  • 4H RSI also oversold (<35): +1% (double confirmation of oversold)
  • Bullish MACD crossover: +0.5–1%

For SELL recommendations use the same format as before:
[TRADE:{"side":"sell","symbol":"SOL-USDT","amountUsdt":50,"partialTpPct":0,"trailingCallbackPct":0,"slPct":0}]
(Leave partialTpPct/trailingCallbackPct/slPct as 0 for sells — they are exits, not entries)

Never include a TRADE tag after a [HOLD]. Use only plain numbers (no commas, no dollar signs).

Always end your response with this exact line:
"⚠ Not financial advice. Crypto is high-risk — only invest what you can afford to lose completely."`;
}

function buildPrompt(type, custom) {
  const techData = state.scannerSymbols.map(sym => {
    const t = state.tickers[sym];
    const ind = state.indicators[sym];
    if (!t) return null;
    const sig = ind?.signal;
    const isDemo = t.source === 'Demo';
    return [
      `**${sym}**${isDemo ? ' [DEMO DATA]' : ''}: ${fmtCrypto(t.price)} (${t.changePercent >= 0 ? '+' : ''}${t.changePercent.toFixed(2)}% 24h)`,
      `  Signal: ${sig?.label ?? '?'} (score ${sig?.score?.toFixed(1) ?? '?'}/9)`,
      `  RSI 1H(14): ${ind?.rsi?.toFixed(1) ?? 'N/A'}`,
      `  RSI 4H(14): ${ind?.rsi4h != null ? ind.rsi4h.toFixed(1) : 'N/A'}`,
      `  MACD: ${ind?.macd ? (ind.macd.bullishCross ? '✓ Bullish crossover (strong buy signal)' : ind.macd.bearishCross ? '✗ Bearish crossover (strong sell signal)' : ind.macd.trend === 'bullish' ? 'Bullish trend' : 'Bearish trend') : 'N/A'}`,
      `  Bollinger %B: ${ind?.bb ? (ind.bb.pctB * 100).toFixed(0) + '%' : 'N/A'} (0%=oversold/lower band, 100%=overbought/upper band)`,
      `  Volume ratio (vs 20-bar avg): ${ind?.volRatio != null ? ind.volRatio.toFixed(2) + 'x' : 'N/A'}`,
      `  Reasons: ${sig?.reasons?.join(', ') || 'Neutral'}`,
    ].join('\n');
  }).filter(Boolean).join('\n\n');

  const portData = state.portfolio.length
    ? state.portfolio.map(pos => {
      const t = state.tickers[pos.symbol];
      const pnlPct = t ? ((t.price - pos.avgBuyPrice) / pos.avgBuyPrice * 100).toFixed(1) : '?';
      return `${pos.symbol}: ${pos.amount} coins @ $${pos.avgBuyPrice} avg cost | Now: ${t ? fmtCrypto(t.price) : '?'} | P&L: ${pnlPct}%`;
    }).join('\n')
    : 'No positions tracked yet.';

  const newsLines = state.news.slice(0, 5).map(n => `- [${n.sentiment.toUpperCase()}] ${n.title}`).join('\n');
  const sentiment = calcNewsSentiment();

  const derivLines = state.scannerSymbols.map(sym => {
    const d = state.derivData[sym];
    if (!d || d.fundingRate === undefined) return null;
    const coin = sym.replace('-USDT', '');
    const frPct = (d.fundingRate * 100).toFixed(4);
    const frBias = d.fundingRate > 0.0005 ? 'longs overheated ⚠'
      : d.fundingRate > 0 ? 'mild bullish bias'
        : d.fundingRate < -0.0005 ? 'shorts overheated ⚠'
          : 'mild bearish bias';
    const oi = d.openInterest !== undefined
      ? d.openInterest.toLocaleString('en-US', { maximumFractionDigits: 0 }) + ' coins'
      : 'N/A';
    return `  ${coin}: Funding ${frPct}% (${frBias}) | OI ${oi}`;
  }).filter(Boolean).join('\n');

  const capitalLine = state.usdtBalance > 0
    ? `Available USDT balance (live from OKX): $${state.usdtBalance.toFixed(2)} — base ALL position sizes on this exact amount`
    : CONFIG.TRADING_CAPITAL > 0
      ? `Trading capital (manually set): $${CONFIG.TRADING_CAPITAL} USDT`
      : 'Trading capital: unknown — do not suggest specific dollar amounts, suggest percentages only';

  const ctx = `
## LIVE TECHNICAL DATA (OKX Spot, ${CONFIG.CANDLE_BAR} candles)
${techData || 'No market data loaded.'}

## DERIVATIVES MARKET CONTEXT (OKX Perpetual Futures)
${derivLines || 'No derivatives data yet — loading in background.'}
Note: High positive funding = longs crowded (risky to buy). High negative = shorts crowded (risky to sell). OI rising with price = strong trend. OI falling = trend weakening.

## MY PORTFOLIO
${portData}

## RECENT NEWS
${newsLines || 'No news.'}
Overall news sentiment: ${sentiment}% bullish

## SESSION INFO
Risk profile: ${CONFIG.RISK_PROFILE}
${capitalLine}
Time: ${new Date().toUTCString()}
`;

  if (type === 'market') return `Give me the best crypto trading opportunities RIGHT NOW based on the technical data.\n\nSpecifically:\n1. Top 2-3 [BUY] opportunities — entry price, take profit %, stop loss %, and why\n2. Any coins I should [SELL] or avoid buying\n3. Overall market direction (bull/bear/sideways)\n4. How long do these signals typically take to play out?\n\n${ctx}`;
  if (type === 'portfolio') return `Review my portfolio and give me specific guidance.\n\nFor each of my holdings:\n1. [HOLD], [BUY MORE], or [SELL] — with reasoning\n2. Suggested take profit level if I should exit\n3. Stop loss level to protect against big drops\n\nAlso identify the best new buying opportunity from the scanner.\n\n${ctx}`;
  if (type === 'risk') return `Perform a risk analysis of my current situation.\n\n1. What is the biggest risk right now in the overall market?\n2. For each of my holdings — what is the worst-case downside scenario?\n3. Suggested stop-loss levels for each position\n4. Should I reduce exposure to anything?\n5. Am I too concentrated in one coin?\n\n${ctx}`;
  if (type === 'custom') return `${custom}\n\n${ctx}`;
  return `Analyze my trading situation.\n\n${ctx}`;
}

function showAiThinking() {
  el('aiResponseArea').innerHTML = `
    <div class="ai-thinking">
      <div class="dot-typing"><span></span><span></span><span></span></div>
      Analyzing ${state.scannerSymbols.length} coins with RSI, MACD, and Bollinger Bands...
    </div>`;
  el('aiFooter').style.display = 'none';
}

function parseTradeActions(text) {
  const results = [];
  const re = /\[TRADE:(\{[^}]+\})\]/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    try {
      const a = JSON.parse(m[1]);
      if (a.side && a.symbol && a.amountUsdt) {
        a.amountUsdt = parseFloat(a.amountUsdt) || 0;
        a.partialTpPct = parseFloat(a.partialTpPct) || 0;
        a.trailingCallbackPct = parseFloat(a.trailingCallbackPct) || 0;
        a.slPct = parseFloat(a.slPct) || 0;
        // Legacy format compatibility
        a.tp = parseFloat(a.tp) || 0;
        a.sl = parseFloat(a.sl) || 0;
        results.push(a);
      }
    } catch { }
  }
  return results;
}

function renderAiResponse(text) {
  const actions = parseTradeActions(text);
  const displayed = text.replace(/\[TRADE:\{[^}]+\}\]/g, '').trim();

  let actionsHtml = '';
  if (actions.length) {
    actionsHtml = '<div class="trade-actions-bar">' +
      actions.map((a, i) => {
        const isBuy = a.side === 'buy';
        const coin = a.symbol.replace('-USDT', '');
        const isO3 = isBuy && a.partialTpPct > 0;
        const detail = isO3
          ? `TP50% +${a.partialTpPct}% · Trail ${a.trailingCallbackPct}% · SL −${a.slPct}%`
          : `$${a.amountUsdt.toLocaleString()} USDT`;
        return `<button class="btn-take-action ${isBuy ? '' : 'sell'}" data-idx="${i}">
          ⚡ ${isO3 ? 'Option 3 Trade' : 'Take Action'} &nbsp;·&nbsp; ${isBuy ? '🟢 BUY' : '🔴 SELL'} ${coin} &nbsp;·&nbsp; ${detail}
        </button>`;
      }).join('') +
      '</div>';
    state._tradeActions = actions;
  }

  // Actions appear first so the user can act immediately.
  // Details are collapsed by default when actions exist, expanded when there are none.
  const hasActions = actions.length > 0;
  el('aiResponseArea').innerHTML =
    actionsHtml +
    `<button class="btn-read-details" id="readDetailsBtn">${hasActions ? 'Read Details ▼' : 'Hide Details ▲'}</button>` +
    `<div class="ai-response" id="aiDetailsContent" style="${hasActions ? 'display:none' : ''}">${markdownToHtml(displayed)}</div>`;

  el('aiFooter').style.display = 'flex';
  el('aiTimestamp').textContent = 'Generated ' + new Date().toLocaleTimeString();

  const detailsBtn = document.getElementById('readDetailsBtn');
  const detailsContent = document.getElementById('aiDetailsContent');
  detailsBtn.addEventListener('click', () => {
    const open = detailsContent.style.display !== 'none';
    detailsContent.style.display = open ? 'none' : 'block';
    detailsBtn.textContent = open ? 'Read Details ▼' : 'Hide Details ▲';
  });

  el('aiResponseArea').querySelectorAll('.btn-take-action').forEach(btn => {
    btn.addEventListener('click', () => showTradeConfirmation(state._tradeActions[+btn.dataset.idx]));
  });
}

function showTradeConfirmation(action) {
  if (!CONFIG.OKX_API_KEY) {
    toast('Add your OKX API key in Settings to place trades', 'error'); return;
  }

  const price = state.tickers[action.symbol]?.price ?? 0;
  const isBuy = action.side === 'buy';
  const coin = action.symbol.replace('-USDT', '');

  const portfolioEntry = state.portfolio.find(p => p.symbol === action.symbol);
  let coinAmt = price > 0 ? action.amountUsdt / price : null;
  if (!isBuy && portfolioEntry && coinAmt > portfolioEntry.amount) coinAmt = portfolioEntry.amount;

  el('tradeConfirmTitle').textContent = `${isBuy ? '🟢 BUY' : '🔴 SELL'} ${coin}`;

  const isO3 = isBuy && action.partialTpPct > 0;

  if (isO3) {
    // ─── Option 3: Partial TP + Trailing Stop ──────────────────────────────
    el('tpSlToggles').style.display = 'none';

    let pTpPct = action.partialTpPct;
    let trailPct = action.trailingCallbackPct || 2.5;
    let slPct = action.slPct || 8;

    function calcO3Prices() {
      return {
        ptPrice: price * (1 + pTpPct / 100),
        slPrice: price * (1 - slPct / 100),
      };
    }

    function updateO3Prices() {
      const { ptPrice, slPrice } = calcO3Prices();
      const ptEl = document.getElementById('o3_ptPrice');
      const slEl = document.getElementById('o3_slPrice');
      const taEl = document.getElementById('o3_trailAct');
      if (ptEl) ptEl.textContent = fmtCrypto(ptPrice);
      if (slEl) slEl.textContent = fmtCrypto(slPrice);
      if (taEl) taEl.textContent = fmtCrypto(ptPrice);
    }

    const { ptPrice, slPrice } = calcO3Prices();
    const coinDisplay = coinAmt ? ` ≈ ${parseFloat(coinAmt.toFixed(6))} ${coin}` : '';

    el('tradeConfirmDetails').innerHTML = `
      <div class="o3-plan">
        <table class="trade-detail-table">
          <tr><td>Buy</td><td>${fmtMoney(action.amountUsdt)} USDT${coinDisplay} at market</td></tr>
        </table>
        <div class="o3-phase-label">Phase 1 — takes effect immediately on OKX (24/7 active)</div>
        <table class="trade-detail-table">
          <tr><td>Sell 50% when price hits</td><td><b id="o3_ptPrice">${fmtCrypto(ptPrice)}</b> <span class="pos">+${pTpPct}%</span></td></tr>
          <tr><td>Stop Loss (full position)</td><td><b id="o3_slPrice">${fmtCrypto(slPrice)}</b> <span class="neg">−${slPct}%</span></td></tr>
        </table>
        <div class="o3-phase-label">Phase 2 — trailing stop on remaining 50% (activates after Phase 1)</div>
        <table class="trade-detail-table">
          <tr><td>Trailing activates at</td><td><span id="o3_trailAct">${fmtCrypto(ptPrice)}</span></td></tr>
          <tr><td>Callback %</td><td>${trailPct}% — follows price up, exits on first reversal</td></tr>
        </table>
        <div class="o3-inputs">
          <label class="o3-inp"><span>Partial TP %</span><input type="number" id="i_pTpPct" value="${pTpPct}" min="1" max="50" step="0.5"></label>
          <label class="o3-inp"><span>Trailing %</span><input type="number" id="i_trailPct" value="${trailPct}" min="0.5" max="20" step="0.5"></label>
          <label class="o3-inp"><span>Stop Loss %</span><input type="number" id="i_slPct" value="${slPct}" min="1" max="50" step="0.5"></label>
        </div>
        <p class="trade-warning">⚠ All 3 orders are placed directly on OKX — they execute automatically 24/7 even when this app is closed.</p>
      </div>`;

    document.getElementById('i_pTpPct').addEventListener('input', e => {
      const v = parseFloat(e.target.value); if (v > 0) { pTpPct = v; updateO3Prices(); }
    });
    document.getElementById('i_trailPct').addEventListener('input', e => {
      const v = parseFloat(e.target.value); if (v > 0) trailPct = v;
    });
    document.getElementById('i_slPct').addEventListener('input', e => {
      const v = parseFloat(e.target.value); if (v > 0) { slPct = v; updateO3Prices(); }
    });

    const btn = el('tradeConfirmBtn');
    btn.disabled = false;
    btn.textContent = '⚡ Confirm Option 3 Trade';
    btn.onclick = () => executeTrade({ ...action, _pTpPct: pTpPct, _trailPct: trailPct, _slPct: slPct }, coinAmt);

  } else {
    // ─── Standard mode ────────────────────────────────────────────────────
    const tpPct = price && action.tp ? ((action.tp - price) / price * 100).toFixed(1) : null;
    const slPct = price && action.sl ? ((price - action.sl) / price * 100).toFixed(1) : null;

    const rows = [
      ['Order Type', `Spot Market ${isBuy ? 'Buy' : 'Sell'} — executes instantly`],
      ['Amount', `${fmtMoney(action.amountUsdt)} USDT${coinAmt ? ` ≈ ${parseFloat(coinAmt.toFixed(6))} ${coin}` : ''}`],
      ['Entry Price', price ? `${fmtCrypto(price)} (current market)` : '? — refresh market data first'],
      action.tp ? ['Take Profit', `${fmtCrypto(action.tp)}${tpPct ? ` <span class="pos">(+${tpPct}%)</span>` : ''}`] : null,
      action.sl ? ['Stop Loss', `${fmtCrypto(action.sl)}${slPct ? ` <span class="neg">(-${slPct}%)</span>` : ''}`] : null,
    ].filter(Boolean);

    el('tradeConfirmDetails').innerHTML =
      `<table class="trade-detail-table">${rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join('')}</table>
       <p class="trade-warning">⚠ TP/SL orders will be placed on OKX after execution based on your toggles below.</p>`;

    const tpSlToggles = el('tpSlToggles');
    const tpToggle = el('tpToggle');
    const slToggle = el('slToggle');
    const hasAlgo = action.tp || action.sl;
    tpSlToggles.style.display = hasAlgo ? 'block' : 'none';
    if (hasAlgo) {
      tpToggle.classList.toggle('active', true);
      tpToggle.textContent = '✓ Take Profit';
      tpToggle.style.display = action.tp ? '' : 'none';
      slToggle.classList.toggle('active', true);
      slToggle.textContent = '✓ Stop Loss';
      slToggle.style.display = action.sl ? '' : 'none';

      function syncTpSlRows() {
        const table = el('tradeConfirmDetails').querySelector('.trade-detail-table');
        if (!table) return;
        table.querySelectorAll('tr').forEach(row => {
          const label = row.cells[0]?.textContent;
          if (label === 'Take Profit') row.style.opacity = tpToggle.classList.contains('active') ? '' : '.35';
          if (label === 'Stop Loss') row.style.opacity = slToggle.classList.contains('active') ? '' : '.35';
        });
      }
      tpToggle.onclick = () => {
        const on = !tpToggle.classList.contains('active');
        tpToggle.classList.toggle('active', on);
        tpToggle.textContent = on ? '✓ Take Profit' : '✕ Take Profit';
        syncTpSlRows();
      };
      slToggle.onclick = () => {
        const on = !slToggle.classList.contains('active');
        slToggle.classList.toggle('active', on);
        slToggle.textContent = on ? '✓ Stop Loss' : '✕ Stop Loss';
        syncTpSlRows();
      };
    }

    const btn = el('tradeConfirmBtn');
    btn.disabled = false;
    btn.textContent = '⚡ Confirm Trade';
    btn.onclick = () => executeTrade(action, coinAmt);
  }

  openModal('tradeConfirmModal');
}

async function saveOption3Trade({ id, symbol, entryPrice, partialTpId, slId, trailingId, amountUsdt, szHalf, partialTpPct, slPct, trailingPct }) {
  if (!isSupabaseConfigured()) return;
  try {
    const res = await fetch(`${getSupabaseCfg().url}/rest/v1/option3_trades`, {
      method: 'POST',
      headers: sbHeaders({ 'Prefer': 'resolution=merge-duplicates,return=minimal' }),
      body: JSON.stringify({
        id, symbol,
        entry_price: entryPrice,
        partial_tp_id: partialTpId,
        sl_id: slId,
        trailing_id: trailingId,
        amount_usdt: amountUsdt,
        sz_half: szHalf,
        partial_tp_pct: partialTpPct,
        sl_pct: slPct,
        trailing_pct: trailingPct,
        phase: 1,
      }),
    });
    if (res.ok) {
      console.log(`[Option3] Trade saved to Supabase — break-even SL monitor active`);
    } else {
      const e = await res.json().catch(() => ({}));
      console.warn('[Option3] Supabase save failed:', e.message || res.status);
    }
  } catch (e) {
    console.warn('[Option3] Supabase save error:', e.message);
  }
}

async function executeTrade(action, coinAmt) {
  const btn = el('tradeConfirmBtn');
  btn.disabled = true;
  btn.textContent = 'Placing order…';

  try {
    const price = state.tickers[action.symbol]?.price;
    if (!price) throw new Error('Current price unavailable — refresh market data and try again');
    const isBuy = action.side === 'buy';
    const szCoin = coinAmt ?? (action.amountUsdt / price);

    // 1. Main market order
    const orderBody = isBuy
      ? { instId: action.symbol, tdMode: 'cash', side: 'buy', ordType: 'market', sz: action.amountUsdt.toFixed(4), tgtCcy: 'quote_ccy' }
      : { instId: action.symbol, tdMode: 'cash', side: 'sell', ordType: 'market', sz: szCoin.toFixed(8) };

    await okxSignedPost('/api/v5/trade/order', orderBody);
    toast(`${action.side.toUpperCase()} order placed`, 'success');

    // ─── Option 3: Partial TP + SL + Trailing Stop ───────────────────────
    if (action._pTpPct > 0 && szCoin > 0) {
      const halfSz = (szCoin * 0.5 * 0.9985).toFixed(8); // 50% with fee haircut
      const fullSz = (szCoin * 1.0 * 0.9985).toFixed(8); // 100% with fee haircut
      const ptPrice = price * (1 + action._pTpPct / 100);
      const slPrice = price * (1 - action._slPct / 100);
      const baseAlgo = { instId: action.symbol, tdMode: 'cash', side: 'sell' };

      btn.textContent = 'Setting up TP/SL/Trailing…';

      const [ptResult, slResult, trailResult] = await Promise.allSettled([
        // Order 2: Conditional — sell 50% at partial TP price
        okxSignedPost('/api/v5/trade/order-algo', {
          ...baseAlgo, ordType: 'conditional', sz: halfSz,
          tpTriggerPx: ptPrice.toFixed(8), tpOrdPx: '-1', tpTriggerPxType: 'last',
        }),
        // Order 3: Conditional — sell 100% at stop loss price
        okxSignedPost('/api/v5/trade/order-algo', {
          ...baseAlgo, ordType: 'conditional', sz: fullSz,
          slTriggerPx: slPrice.toFixed(8), slOrdPx: '-1', slTriggerPxType: 'last',
        }),
        // Order 4: Trailing stop — sell remaining 50%, activates at TP price
        okxSignedPost('/api/v5/trade/order-algo', {
          ...baseAlgo, ordType: 'move_order_stop', sz: halfSz,
          activePx: ptPrice.toFixed(8),
          callbackRatio: (action._trailPct / 100).toFixed(4),
        }),
      ]);

      const labels = ['Partial TP (50%)', 'Stop Loss (100%)', 'Trailing Stop (50%)'];
      const passed = [ptResult, slResult, trailResult].filter(r => r.status === 'fulfilled').length;

      if (passed === 3) {
        toast('Option 3 active: Partial TP + SL + Trailing Stop all set on OKX', 'success', 6000);

        // Extract OKX algo order IDs and save to Supabase for break-even SL monitoring
        const ptId = ptResult.value?.data?.[0]?.algoId;
        const slId = slResult.value?.data?.[0]?.algoId;
        const trailId = trailResult.value?.data?.[0]?.algoId;
        if (ptId && slId && trailId) {
          await saveOption3Trade({
            id: ptId,
            symbol: action.symbol,
            entryPrice: price,
            partialTpId: ptId,
            slId,
            trailingId: trailId,
            amountUsdt: action.amountUsdt,
            szHalf: halfSz,
            partialTpPct: action._pTpPct,
            slPct: action._slPct,
            trailingPct: action._trailPct,
          });
        }
      } else {
        [ptResult, slResult, trailResult].forEach((r, i) => {
          if (r.status === 'rejected') {
            console.error(`[Option3] ${labels[i]} failed:`, r.reason?.message);
            toast(`${labels[i]} could not be set — set manually on OKX. ${r.reason?.message ?? ''}`, 'error', 10000);
          }
        });
        if (passed > 0) toast(`${passed}/3 orders placed — check OKX for details`, 'success', 6000);
      }

    } else {
      // ─── Standard TP/SL (legacy / sell orders) ───────────────────────────
      const useTP = action.tp && el('tpToggle')?.classList.contains('active');
      const useSL = action.sl && el('slToggle')?.classList.contains('active');

      if ((useTP || useSL) && szCoin > 0) {
        const algoSz = (szCoin * 0.9985).toFixed(8);
        const algoSide = isBuy ? 'sell' : 'buy';
        const baseAlgo = { instId: action.symbol, tdMode: 'cash', side: algoSide };

        if (useTP && useSL) {
          try {
            await okxSignedPost('/api/v5/trade/order-algo', {
              ...baseAlgo, ordType: 'oco', sz: algoSz,
              tpTriggerPx: String(action.tp), tpOrdPx: '-1', tpTriggerPxType: 'last',
              slTriggerPx: String(action.sl), slOrdPx: '-1', slTriggerPxType: 'last',
            });
            toast('TP/SL set (OCO order active)', 'success');
          } catch (e) {
            console.warn('[TP/SL] OCO failed, trying separate conditional orders:', e.message);
            try {
              await Promise.all([
                okxSignedPost('/api/v5/trade/order-algo', {
                  ...baseAlgo, ordType: 'conditional', sz: algoSz,
                  tpTriggerPx: String(action.tp), tpOrdPx: '-1', tpTriggerPxType: 'last',
                }),
                okxSignedPost('/api/v5/trade/order-algo', {
                  ...baseAlgo, ordType: 'conditional', sz: algoSz,
                  slTriggerPx: String(action.sl), slOrdPx: '-1', slTriggerPxType: 'last',
                }),
              ]);
              toast('TP/SL set (two conditional orders — cancel one manually after the other triggers)', 'success', 6000);
            } catch (e2) {
              console.error('[TP/SL] Both OCO and conditional orders failed:', e2.message);
              toast('TP/SL could not be set — set manually on OKX. Error: ' + e2.message, 'error', 8000);
            }
          }
        } else if (useTP) {
          try {
            await okxSignedPost('/api/v5/trade/order-algo', {
              ...baseAlgo, ordType: 'conditional', sz: algoSz,
              tpTriggerPx: String(action.tp), tpOrdPx: '-1', tpTriggerPxType: 'last',
            });
            toast('Take Profit set', 'success');
          } catch (e) {
            toast('TP could not be set — set manually on OKX. Error: ' + e.message, 'error', 8000);
          }
        } else {
          try {
            await okxSignedPost('/api/v5/trade/order-algo', {
              ...baseAlgo, ordType: 'conditional', sz: algoSz,
              slTriggerPx: String(action.sl), slOrdPx: '-1', slTriggerPxType: 'last',
            });
            toast('Stop Loss set', 'success');
          } catch (e) {
            toast('SL could not be set — set manually on OKX. Error: ' + e.message, 'error', 8000);
          }
        }
      }
    }

    closeModal('tradeConfirmModal');
    // Refresh balance + portfolio after 2 s
    setTimeout(() => {
      fetchOKXBalance().then(details => {
        const usdt = parseFloat(details.find(d => d.ccy === 'USDT')?.cashBal ?? 0);
        showUsdtBalance(usdt);
      }).catch(() => { });
      syncPortfolioFromOKX().catch(() => { });
    }, 2000);

  } catch (err) {
    toast('Trade failed: ' + err.message, 'error');
    btn.disabled = false;
    btn.textContent = '⚡ Confirm Trade';
  }
}

function renderAiError(msg) {
  el('aiResponseArea').innerHTML = `
    <div style="color:var(--red);font-size:13px;padding:16px;background:var(--red-dim);border-radius:6px;border:1px solid var(--red)">
      <strong>Error:</strong> ${escHtml(msg)}
    </div>`;
  el('aiFooter').style.display = 'none';
}

function markdownToHtml(text) {
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h3>$1</h3>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`(.+?)`/g, '<code style="background:var(--bg-input);padding:1px 4px;border-radius:3px;font-family:var(--mono)">$1</code>')
    .replace(/\[BUY\]/g, '<span class="rec-buy">[BUY]</span>')
    .replace(/\[SELL\]/g, '<span class="rec-sell">[SELL]</span>')
    .replace(/\[HOLD\]/g, '<span class="rec-hold">[HOLD]</span>')
    .replace(/^[-*] (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>[\s\S]*?<\/li>\n?)+/g, m => `<ul>${m}</ul>`)
    .replace(/\n\n/g, '</p><p>')
    .replace(/⚠(.+)/g, '<p class="ai-disclaimer-line">⚠$1</p>')
    .trim();
}

// ═══════════════════════════════════════════════════════════
//  ADD POSITION
// ═══════════════════════════════════════════════════════════
async function handleAddPosition() {
  const raw = el('newSymbol').value.trim().toUpperCase();
  const amount = parseFloat(el('newAmount').value);
  const avgBuyPrice = parseFloat(el('newAvgCost').value);
  const symbol = raw.includes('-') ? raw : raw + '-USDT';

  if (!raw) { toast('Enter a coin symbol', 'error'); return; }
  if (isNaN(amount) || amount <= 0) { toast('Enter a valid amount', 'error'); return; }
  if (isNaN(avgBuyPrice) || avgBuyPrice <= 0) { toast('Enter a valid buy price', 'error'); return; }
  if (state.portfolio.some(p => p.symbol === symbol)) { toast(`${symbol} already in portfolio`, 'error'); return; }

  closeModal('addPositionModal');
  state.portfolio.push({ symbol, amount, avgBuyPrice });
  savePortfolio();

  if (!state.scannerSymbols.includes(symbol)) {
    state.scannerSymbols.push(symbol);
    saveScannerSymbols();
  }

  toast(`Fetching live data for ${symbol}...`, 'info');
  await fetchSymbolData(symbol);
  renderPortfolio();
  renderScanner();

  ['newSymbol', 'newAmount', 'newAvgCost'].forEach(id => el(id).value = '');
  toast(`Added ${symbol} to portfolio`, 'success');
}

// ═══════════════════════════════════════════════════════════
//  REFRESH
// ═══════════════════════════════════════════════════════════
async function refreshAll() {
  if (state.isRefreshing) return;
  state.isRefreshing = true;
  el('refreshBtn').classList.add('spinning');

  await fetchAllData();
  renderPortfolio();
  renderScanner();
  checkSignalAlerts();

  state.isRefreshing = false;
  el('refreshBtn').classList.remove('spinning');
  toast('Data refreshed', 'success');
}

async function refreshNews(topic = '') {
  el('newsList').innerHTML = '<div class="loading-row"><span class="spinner"></span> Loading news...</div>';
  await fetchNews(topic);
  renderNews();
}

function restartAutoRefresh() {
  clearInterval(state.refreshTimer);
  clearInterval(state.newsTimer);
  if (CONFIG.AUTO_REFRESH_INTERVAL > 0) {
    state.refreshTimer = setInterval(refreshAll, CONFIG.AUTO_REFRESH_INTERVAL);
    state.newsTimer = setInterval(() => refreshNews(), CONFIG.NEWS_REFRESH_INTERVAL);
  }
}

// ═══════════════════════════════════════════════════════════
//  PERSISTENCE
// ═══════════════════════════════════════════════════════════
function loadPortfolio() { state.portfolio = LS.get('portfolio', CONFIG.DEFAULT_PORTFOLIO); }
function savePortfolio() {
  LS.set('portfolio', state.portfolio);
  LS.set('portfolioUpdated', Date.now());
  pushPortfolioSymbols();  // symbols list for signal checker
  pushFullPortfolio();     // full data for cross-device sync
}

async function pushPortfolioSymbols() {
  if (!isSupabaseConfigured()) return;
  const symbols = state.portfolio.map(p => p.symbol).join(',');
  try {
    const r = await fetch(`${getSupabaseCfg().url}/rest/v1/app_settings?id=eq.main`, {
      method: 'PATCH',
      headers: { ...sbHeaders(), 'Prefer': 'return=minimal' },
      body: JSON.stringify({ portfolio_symbols: symbols }),
    });
    if (!r.ok) console.error('[portfolio sync] Supabase PATCH failed:', r.status, await r.text().catch(() => ''));
  } catch (e) {
    console.error('[portfolio sync] network error:', e.message);
  }
}

async function pushFullPortfolio() {
  if (!isSupabaseConfigured() || !state.portfolio.length) return;
  try {
    await fetch(`${getSupabaseCfg().url}/rest/v1/app_settings?id=eq.main`, {
      method: 'PATCH',
      headers: { ...sbHeaders(), 'Prefer': 'return=minimal' },
      body: JSON.stringify({
        portfolio_data:    JSON.stringify(state.portfolio),
        portfolio_updated: new Date().toISOString(),
      }),
    });
  } catch { }
}

async function pullPortfolioFromCloud() {
  if (!isSupabaseConfigured()) return false;
  try {
    const r = await fetch(
      `${getSupabaseCfg().url}/rest/v1/app_settings?id=eq.main&select=portfolio_data,portfolio_updated`,
      { headers: sbHeaders() },
    );
    if (!r.ok) return false;
    const rows = await r.json();
    const raw  = rows[0]?.portfolio_data;
    if (!raw) return false;
    const cloudPortfolio = JSON.parse(raw);
    if (!Array.isArray(cloudPortfolio) || !cloudPortfolio.length) return false;

    // Only overwrite local if cloud is newer (or local is empty)
    const cloudTs = rows[0]?.portfolio_updated ? new Date(rows[0].portfolio_updated).getTime() : 0;
    const localTs = LS.get('portfolioUpdated', 0);
    if (cloudTs > localTs || !state.portfolio.length) {
      state.portfolio = cloudPortfolio;
      LS.set('portfolio', state.portfolio);
      LS.set('portfolioUpdated', cloudTs || Date.now());
      return true;
    }
  } catch { }
  return false;
}
function loadScannerSymbols() { state.scannerSymbols = LS.get('scanner', CONFIG.DEFAULT_SCANNER); }
function saveScannerSymbols() { LS.set('scanner', state.scannerSymbols); }

// ═══════════════════════════════════════════════════════════
//  EXPORT / IMPORT
// ═══════════════════════════════════════════════════════════
function exportData() {
  const blob = new Blob([JSON.stringify({ portfolio: state.portfolio, scanner: state.scannerSymbols }, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement('a'), { href: url, download: `crypto-advisor-${new Date().toISOString().slice(0, 10)}.json` });
  a.click();
  URL.revokeObjectURL(url);
  toast('Exported', 'success');
}

function importData(file) {
  const reader = new FileReader();
  reader.onload = e => {
    try {
      const data = JSON.parse(e.target.result);
      if (data.portfolio) { state.portfolio = data.portfolio; savePortfolio(); }
      if (data.scanner) { state.scannerSymbols = data.scanner; saveScannerSymbols(); }
      toast('Imported successfully', 'success');
      closeModal('settingsModal');
      refreshAll();
    } catch { toast('Invalid JSON file', 'error'); }
  };
  reader.readAsText(file);
}

function clearAllData() {
  if (!confirm('Reset all portfolio data and settings? This cannot be undone.')) return;
  localStorage.clear();
  state.portfolio = [];
  state.scannerSymbols = [...CONFIG.DEFAULT_SCANNER];
  savePortfolio();
  saveScannerSymbols();
  toast('Reset to defaults', 'info');
  closeModal('settingsModal');
  refreshAll();
}

// ═══════════════════════════════════════════════════════════
//  MODALS + CLOCK
// ═══════════════════════════════════════════════════════════
function openModal(id) { el(id).classList.add('open'); }
function closeModal(id) { el(id).classList.remove('open'); }

// ═══════════════════════════════════════════════════════════
//  UTILITIES
// ═══════════════════════════════════════════════════════════
function el(id) { return document.getElementById(id); }

function fmtCrypto(n) {
  if (n >= 10000) return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (n >= 1000) return '$' + n.toFixed(2);
  if (n >= 1) return '$' + n.toFixed(4);
  if (n >= 0.01) return '$' + n.toFixed(5);
  return '$' + n.toFixed(7);
}

function fmtMoney(n) {
  if (Math.abs(n) >= 1_000_000) return '$' + (n / 1_000_000).toFixed(2) + 'M';
  if (Math.abs(n) >= 1_000) return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return '$' + n.toFixed(2);
}

function timeAgo(date) {
  const s = (Date.now() - date.getTime()) / 1000;
  if (s < 60) return Math.round(s) + 's ago';
  if (s < 3600) return Math.round(s / 60) + 'm ago';
  if (s < 86400) return Math.round(s / 3600) + 'h ago';
  return Math.round(s / 86400) + 'd ago';
}

function escHtml(str) {
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function toast(msg, type = 'info', duration = 3500) {
  const div = Object.assign(document.createElement('div'), { className: `toast ${type}`, textContent: msg });
  el('toastContainer').appendChild(div);
  setTimeout(() => div.remove(), duration);
}

// ═══════════════════════════════════════════════════════════
//  EVENT WIRING
// ═══════════════════════════════════════════════════════════
function wireEvents() {
  el('refreshBtn').addEventListener('click', refreshAll);
  el('settingsBtn').addEventListener('click', () => { populateSettingsForm(); openModal('settingsModal'); });

  // Portfolio
  el('okxSyncBtn').addEventListener('click', syncPortfolioFromOKX);
  el('addPositionBtn').addEventListener('click', () => openModal('addPositionModal'));
  el('portfolioRefreshBtn').addEventListener('click', refreshAll);
  el('confirmAddPosition').addEventListener('click', handleAddPosition);
  el('newSymbol').addEventListener('keydown', e => { if (e.key === 'Enter') el('newAmount').focus(); });
  el('newAmount').addEventListener('keydown', e => { if (e.key === 'Enter') el('newAvgCost').focus(); });
  el('newAvgCost').addEventListener('keydown', e => { if (e.key === 'Enter') handleAddPosition(); });

  // Scanner
  el('scannerAddBtn').addEventListener('click', () => {
    const raw = el('scannerAddInput').value.trim().toUpperCase();
    const sym = raw.includes('-') ? raw : raw + '-USDT';
    if (!raw) return;
    if (state.scannerSymbols.includes(sym)) { toast(`${sym} already tracked`, 'info'); return; }
    state.scannerSymbols.push(sym);
    saveScannerSymbols();
    el('scannerAddInput').value = '';
    fetchSymbolData(sym).then(() => renderScanner());
    toast(`Added ${sym} to scanner`, 'success');
  });
  el('scannerAddInput').addEventListener('keydown', e => { if (e.key === 'Enter') el('scannerAddBtn').click(); });
  el('scannerSortSelect').addEventListener('change', () => { state.scannerSort = el('scannerSortSelect').value; renderScanner(); });

  document.querySelectorAll('.filter-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      state.scannerFilter = chip.dataset.filter;
      renderScanner();
    });
  });

  // News
  el('newsSearchBtn').addEventListener('click', () => refreshNews(el('newsTopicInput').value.trim()));
  el('newsTopicInput').addEventListener('keydown', e => { if (e.key === 'Enter') el('newsSearchBtn').click(); });

  // AI
  el('aiAnalyzeBtn').addEventListener('click', runAiAnalysis);
  el('aiContextSelect').addEventListener('change', () => {
    el('aiCustomQuery').style.display = el('aiContextSelect').value === 'custom' ? 'block' : 'none';
  });
  el('aiCustomInput').addEventListener('keydown', e => { if (e.key === 'Enter' && e.ctrlKey) runAiAnalysis(); });

  // Lock screen
  el('lockUnlockBtn').addEventListener('click', handleUnlock);
  el('lockPasswordInput').addEventListener('keydown', e => { if (e.key === 'Enter') handleUnlock(); });

  // Save all to cloud
  el('saveToCloudBtn').addEventListener('click', async () => {
    const password = el('settingsCloudPassword').value.trim();
    if (!password) { toast('Enter a cloud password first', 'error'); return; }
    const btn = el('saveToCloudBtn');
    btn.disabled = true;
    btn.textContent = 'Saving…';
    try {
      LS.set('supabaseCfg', { url: el('settingsSbUrl').value.trim(), key: el('settingsSbKey').value.trim() });
      CONFIG.CLAUDE_API_KEY = el('settingsClaudeKey').value.trim();
      CONFIG.OKX_API_KEY = el('settingsOkxKey').value.trim();
      CONFIG.OKX_SECRET_KEY = el('settingsOkxSecret').value.trim();
      CONFIG.OKX_PASSPHRASE = el('settingsOkxPassphrase').value.trim();
      CONFIG.TELEGRAM_BOT_TOKEN = el('settingsTgToken').value.trim();
      CONFIG.TELEGRAM_CHAT_ID = el('settingsTgChatId').value.trim();
      CONFIG.RISK_PROFILE = el('settingsRiskProfile').value;
      CONFIG.AUTO_REFRESH_INTERVAL = parseInt(el('settingsRefreshInterval').value);
      await saveToCloud(password);
      state.sessionPassword = password;
      el('settingsCloudPassword').value = '';
      toast('All settings saved to cloud ✓', 'success');
    } catch (err) {
      toast('Cloud save failed: ' + err.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Save All to Cloud';
    }
  });

  // Settings
  el('saveSettingsBtn').addEventListener('click', saveSettings);
  el('exportDataBtn').addEventListener('click', exportData);
  el('importDataBtn').addEventListener('click', () => el('importFileInput').click());
  el('importFileInput').addEventListener('change', e => { if (e.target.files[0]) importData(e.target.files[0]); });
  el('clearDataBtn').addEventListener('click', clearAllData);

  // Close modals
  document.querySelectorAll('.modal-close').forEach(btn => btn.addEventListener('click', () => closeModal(btn.dataset.modal)));
  document.querySelectorAll('.modal-overlay').forEach(o => o.addEventListener('click', e => { if (e.target === o) closeModal(o.id); }));
  document.addEventListener('keydown', e => { if (e.key === 'Escape') document.querySelectorAll('.modal-overlay.open').forEach(m => closeModal(m.id)); });
}

// ═══════════════════════════════════════════════════════════
//  INIT
// ═══════════════════════════════════════════════════════════
async function loadAppData() {
  // Restore last known USDT balance immediately (before sync)
  const lastBal = LS.get('lastUsdtBalance', 0);
  if (lastBal > 0) showUsdtBalance(lastBal);

  // Pull portfolio from Supabase — keeps all devices in sync automatically
  await pullPortfolioFromCloud();

  renderPortfolio();
  renderScanner();
  await fetchAllData();
  renderPortfolio();
  renderScanner();
  checkSignalAlerts();
  refreshNews();
  restartAutoRefresh();
  // Auto-sync OKX balance if credentials are available
  if (CONFIG.OKX_API_KEY && CONFIG.OKX_SECRET_KEY && CONFIG.OKX_PASSPHRASE) {
    syncPortfolioFromOKX().catch(err => toast('OKX auto-sync failed: ' + err.message, 'error'));
  }
}

async function init() {
  loadSettings();
  loadPortfolio();
  state.notifiedSignals = LS.get('notifiedSignals', {});
  loadScannerSymbols();
  wireEvents();
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('./sw.js').catch(() => { });

  // If Supabase is configured, try auto-unlock from session first
  if (isSupabaseConfigured()) {
    const saved = localStorage.getItem('sp');
    if (saved) {
      try {
        const data = await loadFromCloud(saved);
        if (data.claude) CONFIG.CLAUDE_API_KEY = data.claude;
        if (data.okxKey) CONFIG.OKX_API_KEY = data.okxKey;
        if (data.okxSecret) CONFIG.OKX_SECRET_KEY = data.okxSecret;
        if (data.okxPassphrase) CONFIG.OKX_PASSPHRASE = data.okxPassphrase;
        if (data.tgToken) CONFIG.TELEGRAM_BOT_TOKEN = data.tgToken;
        if (data.tgChatId) CONFIG.TELEGRAM_CHAT_ID = data.tgChatId;
        if (data.riskProfile) CONFIG.RISK_PROFILE = data.riskProfile;
        if (data.refreshInterval) CONFIG.AUTO_REFRESH_INTERVAL = parseInt(data.refreshInterval);
        state.sessionPassword = saved;
        await loadAppData();
        return;
      } catch {
        localStorage.removeItem('sp');
      }
    }
    el('lockScreen').style.display = 'flex';
    el('lockPasswordInput').focus();
    return;
  }

  // No cloud setup — load app normally
  await loadAppData();
}

document.addEventListener('DOMContentLoaded', init);
