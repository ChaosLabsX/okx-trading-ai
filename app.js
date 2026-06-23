'use strict';

// ═══════════════════════════════════════════════════════════
//  STATE
// ═══════════════════════════════════════════════════════════
const state = {
  portfolio: [],        // populated transiently during AI analysis from live OKX data
  scannerSymbols: [],   // ['BTC-USDT', ...]
  tickers: {},          // {symbol: {price, change, changePercent, high24h, low24h, vol24h, source}}
  indicators: {},       // {symbol: {rsi, macd, bb, signal}}
  signalTimes: JSON.parse(localStorage.getItem('signalTimes') || '{}'), // {symbol: {label, enteredAt}}
  news: [],
  scannerFilter: 'all',
  scannerSort: 'signal',
  refreshTimer: null,
  newsTimer: null,
  isRefreshing: false,
  notifiedSignals: {},   // {symbol: lastNotifiedLabel} — prevents duplicate alerts
  alertsWarmedUp: false, // true after first scan — suppresses alerts on page load
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
    const [ticker, candles1H, candles4H, candles30m] = await Promise.all([
      fetchOKXTicker(symbol),
      fetchOKXCandles(symbol, '1H'),
      fetchOKXCandles(symbol, '4H'),
      fetchOKXCandles(symbol, '30m'),
    ]);
    state.tickers[symbol] = ticker;
    state.indicators[symbol] = computeIndicators(candles1H, candles4H, candles30m);
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
  const symbols = [...state.scannerSymbols];
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
    sessionStorage.setItem('sp', password);
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
  if (!CONFIG.TELEGRAM_BOT_TOKEN || !CONFIG.TELEGRAM_CHAT_ID) {
    toast('⚠ Telegram not configured — add Bot Token and Chat ID in Settings', 'error', 8000);
    return false;
  }
  try {
    const res = await fetch(`https://api.telegram.org/bot${CONFIG.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: CONFIG.TELEGRAM_CHAT_ID, text: message, parse_mode: 'HTML' }),
    });
    const data = await res.json();
    if (!data.ok) {
      toast(`⚠ Telegram error: ${data.description ?? 'unknown error'}`, 'error', 8000);
      return false;
    }
    return true;
  } catch (e) {
    toast(`⚠ Telegram send failed: ${e.message}`, 'error', 8000);
    return false;
  }
}

function reversalWhyFailed(ind) {
  if (!ind) return 'no indicator data';
  // Use 30min data for the message if available, fall back to 1H
  const close    = ind.lastClose30m ?? ind.lastClose;
  const open     = ind.lastOpen30m  ?? ind.lastOpen;
  const rsi      = ind.rsi30m       ?? ind.rsi;
  const rsiPrev  = ind.rsiPrev30m   ?? ind.rsiPrev;
  const volRatio = ind.volRatio30m  ?? ind.volRatio;
  const tf       = ind.lastClose30m != null ? '30min' : '1H';
  const reasons  = [];
  if (close != null && open != null && close < open)
    reasons.push(`${tf} candle still red`);
  if (rsi != null && rsiPrev != null && rsi < rsiPrev)
    reasons.push(`${tf} RSI still falling`);
  if (volRatio != null && volRatio < 1.0)
    reasons.push(`${tf} volume only ${volRatio.toFixed(2)}× avg (need ≥ 1.0×)`);
  return reasons.length ? reasons.join(', ') : 'unknown';
}

function reversalConfirmedBrowser(ind, zone) {
  if (!ind) return true;
  // Prefer 30min candle for earlier entry — fall back to 1H if 30min unavailable
  const open     = ind.lastOpen30m  ?? ind.lastOpen;
  const close    = ind.lastClose30m ?? ind.lastClose;
  const rsi      = ind.rsi30m       ?? ind.rsi;
  const rsiPrev  = ind.rsiPrev30m   ?? ind.rsiPrev;
  const volRatio = ind.volRatio30m  ?? ind.volRatio;
  if (open == null || close == null || rsi == null || rsiPrev == null) return true;
  const volOk = volRatio == null || volRatio >= 1.0;
  if (zone === 'up')   return close >= open && rsi >= rsiPrev && volOk;
  if (zone === 'down') return close <= open && rsi <= rsiPrev && volOk;
  return true;
}

async function checkSignalAlerts() {
  // On the first scan after page load, silently mark all active STRONG BUY coins
  // as already notified so we don't duplicate an alert the Python script already sent.
  if (!state.alertsWarmedUp) {
    for (const sym of state.scannerSymbols) {
      if (state.tickers[sym]?.source === 'Demo') continue;
      const sig = state.indicators[sym]?.signal;
      if (sig?.label === 'STRONG BUY') {
        state.notifiedSignals[sym] = 'up';
      }
    }
    LS.set('notifiedSignals', state.notifiedSignals);
    state.alertsWarmedUp = true;
    return;
  }

  for (const sym of state.scannerSymbols) {
    if (state.tickers[sym]?.source === 'Demo') continue;
    const sig = state.indicators[sym]?.signal;
    const ind = state.indicators[sym];
    const price = state.tickers[sym]?.price;
    if (!sig || !price) continue;

    const shouldAlert = sig.label === 'STRONG BUY';
    const currentZone = shouldAlert ? 'up' : 'neutral';
    const lastZone = state.notifiedSignals[sym] ?? 'neutral';
    const alreadyNotified = shouldAlert && currentZone === lastZone;
    const coin = sym.replace('-USDT', '');

    if (shouldAlert && !alreadyNotified) {
      if (!reversalConfirmedBrowser(ind, currentZone)) {
        // Signal is STRONG BUY but reversal not confirmed yet — tell the user why
        toast(`⏳ STRONG BUY: ${coin} — waiting for reversal (${reversalWhyFailed(ind)})`, 'info', 7000);
        continue;
      }

      // Telegram for STRONG BUY is handled entirely by the Python script (auto-trade).
      // Browser only shows an on-screen toast so the user sees live signals when the app is open.
      toast(`🟢 STRONG BUY: ${coin} — auto-trade system will handle this`, 'success', 7000);

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

async function okxProxyFetch(url, options = {}) {
  // Two proxy attempts with a 1-second gap — guards against temporary corsproxy.io outages.
  // OKX signatures are timestamp-bound so we must re-sign on retry (handled by callers).
  const proxy = u => `https://corsproxy.io/?${encodeURIComponent(u)}`;
  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      const res = await fetch(proxy(url), options);
      if (res.ok) return res;
      throw new Error(`HTTP ${res.status}`);
    } catch (err) {
      if (attempt === 2) throw err;
      await new Promise(r => setTimeout(r, 1000));
    }
  }
}

async function okxSignedGet(path) {
  const timestamp = new Date().toISOString();
  const sign = await hmacSHA256base64(CONFIG.OKX_SECRET_KEY, timestamp + 'GET' + path);
  const res = await okxProxyFetch(CONFIG.OKX_BASE + path, {
    headers: {
      'OK-ACCESS-KEY': CONFIG.OKX_API_KEY,
      'OK-ACCESS-SIGN': sign,
      'OK-ACCESS-TIMESTAMP': timestamp,
      'OK-ACCESS-PASSPHRASE': CONFIG.OKX_PASSPHRASE,
      'Content-Type': 'application/json',
    },
  });
  const d = await res.json();
  if (d.code !== '0') throw new Error(d.msg || `OKX error code ${d.code}`);
  return d;
}

async function okxSignedPost(path, body) {
  const timestamp = new Date().toISOString();
  const bodyStr = JSON.stringify(body);
  const sign = await hmacSHA256base64(CONFIG.OKX_SECRET_KEY, timestamp + 'POST' + path + bodyStr);
  const res = await okxProxyFetch(CONFIG.OKX_BASE + path, {
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

function computeIndicators(candles1H, candles4H = [], candles30m = []) {
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

  // 1H volume ratio: latest candle vs 20-bar average
  let volRatio = null;
  if (volumes.length >= 21) {
    const avg = volumes.slice(-21, -1).reduce((a, b) => a + b, 0) / 20;
    if (avg > 0) volRatio = volumes[volumes.length - 1] / avg;
  }

  // 1H last candle — fallback if 30m unavailable
  const lastOpen = opens.at(-1) ?? null;
  const lastClose = closes.at(-1) ?? null;

  // 30min reversal data — used for earlier entry confirmation
  const opens30m   = candles30m.map(c => c.open);
  const closes30m  = candles30m.map(c => c.close);
  const volumes30m = candles30m.map(c => c.vol);
  const lastOpen30m  = opens30m.at(-1) ?? null;
  const lastClose30m = closes30m.at(-1) ?? null;
  const rsi30m     = closes30m.length > 14 ? calcRSI(closes30m) : null;
  const rsiPrev30m = closes30m.length > 15 ? calcRSI(closes30m.slice(0, -1)) : null;
  let volRatio30m = null;
  if (volumes30m.length >= 21) {
    const avg = volumes30m.slice(-21, -1).reduce((a, b) => a + b, 0) / 20;
    if (avg > 0) volRatio30m = volumes30m[volumes30m.length - 1] / avg;
  }

  return { rsi, rsiPrev, rsi4h, macd, bb, volRatio, lastOpen, lastClose,
           lastOpen30m, lastClose30m, rsi30m, rsiPrev30m, volRatio30m,
           signal: generateSignal(rsi, macd, bb, rsi4h, volRatio) };
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
    else if (rsi <= 40) { reasons.push(`RSI ${rsi.toFixed(0)} — below neutral`); }  // no score bonus
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
    if (score > 0 && rsi4h <= 40) { score += 1; reasons.push(`4H RSI ${rsi4h.toFixed(0)} — higher-TF uptrend confirmed`); }
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
  if (score >= 5) { label = 'STRONG BUY'; cls = 'sig-sbuy'; }
  else if (score >= 2) { label = 'BUY'; cls = 'sig-buy'; }
  else if (score > -2) { label = 'HOLD'; cls = 'sig-hold'; }
  else if (score > -5) { label = 'SELL'; cls = 'sig-sell'; }
  else { label = 'STRONG SELL'; cls = 'sig-ssell'; }

  return { score, label, cls, reasons };
}

function fmtAge(ms) {
  const s = Math.floor(ms / 1000);
  if (s < 60)  return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60)  return `${m}m ${String(s % 60).padStart(2, '0')}s ago`;
  const h = Math.floor(m / 60);
  if (h < 24)  return `${h}h ${m % 60}m ago`;
  return `${Math.floor(h / 24)}d ${h % 24}h ago`;
}

function startAgeTicker() {
  setInterval(() => {
    document.querySelectorAll('.signal-age-cell[data-sym]').forEach(cell => {
      const sym = cell.dataset.sym;
      const st  = state.signalTimes[sym];
      const sig = state.indicators[sym]?.signal;
      if (!st || !sig || st.label !== sig.label) return;
      cell.textContent = fmtAge(Date.now() - st.enteredAt);
      const _d = new Date(st.enteredAt);
      cell.title = `${sig.label} since ${_d.getUTCHours().toString().padStart(2,'0')}:${_d.getUTCMinutes().toString().padStart(2,'0')} UTC`;
    });
  }, 1000);
}

function updateSignalTimes() {
  const now = Date.now();
  // Floor to UTC hour boundary — Date.now() is always UTC ms, so this value is
  // identical on every device regardless of local timezone.
  const hourBoundary = Math.floor(now / 3600000) * 3600000;
  for (const sym of state.scannerSymbols) {
    const label = state.indicators[sym]?.signal?.label;
    if (!label) continue;
    const stored = state.signalTimes[sym];
    if (!stored || stored.label !== label) {
      state.signalTimes[sym] = { label, enteredAt: hourBoundary };
    }
  }
  localStorage.setItem('signalTimes', JSON.stringify(state.signalTimes));
}

// ═══════════════════════════════════════════════════════════
//  RENDER — SCANNER
// ═══════════════════════════════════════════════════════════
function renderScanner() {
  const tbody = el('scannerBody');
  const countEl = el('scannerCount');
  if (countEl) countEl.textContent = state.scannerSymbols.length;
  let symbols = state.scannerSymbols.filter(s => state.tickers[s]?.source !== 'Demo');

  // Filter
  if (state.scannerFilter === 'buy') symbols = symbols.filter(s => (state.indicators[s]?.signal.score ?? 0) >= 2);
  if (state.scannerFilter === 'sell') symbols = symbols.filter(s => (state.indicators[s]?.signal.score ?? 0) <= -2);
  if (state.scannerFilter === 'hold') symbols = symbols.filter(s => { const sc = state.indicators[s]?.signal.score ?? 0; return sc > -2 && sc < 2; });

  // Sort
  symbols.sort((a, b) => {
    if (state.scannerSort === 'signal') {
      const order = { 'STRONG BUY': 0, 'BUY': 1, 'STRONG SELL': 2, 'SELL': 3, 'HOLD': 4 };
      const aLbl = state.indicators[a]?.signal.label ?? 'HOLD';
      const bLbl = state.indicators[b]?.signal.label ?? 'HOLD';
      const pri = (order[aLbl] ?? 4) - (order[bLbl] ?? 4);
      if (pri !== 0) return pri;
      return Math.abs(state.indicators[b]?.signal.score ?? 0) - Math.abs(state.indicators[a]?.signal.score ?? 0);
    }
    if (state.scannerSort === 'change') return (state.tickers[b]?.changePercent ?? 0) - (state.tickers[a]?.changePercent ?? 0);
    if (state.scannerSort === 'price') return (state.tickers[b]?.price ?? 0) - (state.tickers[a]?.price ?? 0);
    return a.localeCompare(b);
  });

  if (!symbols.length) {
    tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--text-muted);padding:28px">No results for this filter.</td></tr>';
    updateBestPickBanner();
    updateSummaryBar();
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

    const reason = sig?.reasons?.join(' · ') || 'Neutral — no strong signal';
    const coin = sym.replace('-USDT', '').replace('-BTC', '');
    const tooltip = escHtml(`Score: ${scoreStr} | ${reason}`);

    const st = state.signalTimes[sym];
    const sigAge = (st && st.label === sig?.label)
      ? fmtAge(Date.now() - st.enteredAt)
      : '—';
    const sigAgeTitle = (() => {
      if (!st || st.label !== sig?.label) return '';
      const _d = new Date(st.enteredAt);
      const utc = `${_d.getUTCHours().toString().padStart(2,'0')}:${_d.getUTCMinutes().toString().padStart(2,'0')} UTC`;
      return escHtml(`${sig?.label} since ${utc}`);
    })();

    return `<tr class="scanner-row ${sig?.cls ?? ''}">
      <td>
        <div class="sym-cell">
          <span class="coin-icon">${coin}</span>
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
      <td class="num signal-age-cell" data-sym="${sym}" title="${sigAgeTitle}">${sigAge}</td>
    </tr>`;
  }).join('');

  updateBestPickBanner();
  updateSummaryBar();
}

function updateBestPickBanner() {
  const banner = el('bestPickBar');
  let best = null, bestScore = -99;
  for (const sym of state.scannerSymbols) {
    if (state.tickers[sym]?.source === 'Demo') continue;
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

function updateSummaryBar() {
  el('lastUpdated').textContent = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });

  const topEl = el('topSignal');
  let best = null, bestScore = -99;
  for (const sym of state.scannerSymbols) {
    if (state.tickers[sym]?.source === 'Demo') continue;
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
  if (!CONFIG.CLAUDE_API_KEY) {
    renderAiError('Add your Claude API key in Settings (⚙ icon) to enable AI analysis. Get a free key at console.anthropic.com');
    return;
  }

  showAiThinking();

  // Fetch live OKX portfolio data so the AI always sees your real current holdings,
  // not a potentially stale snapshot. Falls back to app state if credentials are missing.
  const prevUsdtBal  = state.usdtBalance;
  const prevPortfolio = state.portfolio;
  if (CONFIG.OKX_API_KEY && CONFIG.OKX_SECRET_KEY && CONFIG.OKX_PASSPHRASE) {
    try {
      const details    = await fetchOKXBalance();
      const usdtDetail = details.find(d => d.ccy === 'USDT');
      const liveBal    = parseFloat(usdtDetail?.cashBal ?? usdtDetail?.availBal ?? '0') || 0;
      if (liveBal > 0) state.usdtBalance = liveBal;

      const liveHoldings = [];
      for (const d of details) {
        if (d.ccy === 'USDT') continue;
        const avail = parseFloat(d.availBal ?? '0') || 0;
        if (avail <= 0) continue;
        const sym   = d.ccy + '-USDT';
        const price = state.tickers[sym]?.price ?? 0;
        if (price > 0 && avail * price < 1) continue; // skip dust
        const avgPx = parseFloat(d.accAvgPx ?? '0') || parseFloat(d.openAvgPx ?? '0') || 0;
        liveHoldings.push({ symbol: sym, amount: avail, avgBuyPrice: avgPx });
      }
      if (liveHoldings.length) state.portfolio = liveHoldings;
    } catch { /* OKX unreachable — use existing app state */ }
  }

  try {
    const systemPrompt = buildSystemPrompt();
    const userPrompt   = buildPrompt();

    // Restore state after prompt is built — don't affect the UI display
    state.usdtBalance = prevUsdtBal;
    state.portfolio   = prevPortfolio;

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
        system: systemPrompt,
        messages: [{ role: 'user', content: userPrompt }],
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error?.message || `HTTP ${res.status}`);
    }

    const data = await res.json();
    renderAiResponse(data.content?.[0]?.text ?? '(No response)');
  } catch (err) {
    // Ensure state is always restored even if Claude call fails
    state.usdtBalance = prevUsdtBal;
    state.portfolio   = prevPortfolio;
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

  return `You are an expert cryptocurrency trading advisor specializing in technical analysis for OKX spot markets. The user relies on you 100% for trade decisions — your analysis must be rigorous, data-driven, and conservative. Only recommend trades with genuine multi-indicator confluence.

CONTEXT:
- Risk profile: ${CONFIG.RISK_PROFILE}
- ${capital}
- ${positionSize}
- Platform: OKX Spot Trading (no leverage, no futures)
- Coins user currently holds (live from OKX): ${ownedCoins}

ANALYSIS RULES — apply these strictly:
1. Only recommend BUY when ALL of the following are true:
   • Signal score ≥ 5.0 (STRONG BUY zone)
   • At least 2 of these confirm: RSI oversold, MACD bullish crossover, BB lower band touch, volume spike
   • 4H RSI < 55 (not overbought on the higher timeframe)
   • Funding rate is not extreme (avoid buying when funding > 0.05% — longs are overheated)
2. WHEN NOT TO TRADE — explicitly say "SKIP" when:
   • Score is 2–3 (BUY zone) but indicators are mixed — weak setup, not worth the risk
   • Price is near a recent high (BB %B > 75%) — late entry, bad risk/reward
   • 4H RSI > 65 — higher timeframe is overbought, pullback likely
   • Funding rate > 0.05% — market is overleveraged long, squeeze risk
   • News sentiment is bearish — macro headwind against the trade
   • Volume is below average — move lacks conviction
3. Confidence level: High (3+ confirmations), Medium (2 confirmations), Low (1 — avoid or reduce size)
4. Use ### headings and bullet points. Be direct and concise.

TRADE TAGS — for every actionable BUY, append this tag on its own line (valid JSON, numbers only):

[TRADE:{"side":"buy","symbol":"AVAX-USDT","amountUsdt":70,"partialTpPct":5,"trailingCallbackPct":3,"slPct":8}]

  • symbol: exact OKX instrument ID (e.g. "AVAX-USDT", "SOL-USDT")
  • amountUsdt: USDT to spend (user can edit this in the confirmation dialog)
  • partialTpPct: % gain at which to sell 50% and lock profit (Phase 1)
  • trailingCallbackPct: trailing stop callback % for remaining 50% after Phase 1 (Phase 2)
  • slPct: initial stop loss % below entry (full position protection before Phase 1)

OPTION 3 PARAMETERS — base on coin volatility AND signal strength:

Extreme volatility (PEPE, WIF, DOGE): partialTpPct 6-8, trailingCallbackPct 3-4, slPct 8-10
High volatility (AVAX, SOL, SUI, INJ, TIA): partialTpPct 4-6, trailingCallbackPct 2.5-3, slPct 7-8
Medium-high (NEAR, APT, FET, LINK, SEI): partialTpPct 3-5, trailingCallbackPct 2-2.5, slPct 6-7

Adjust partialTpPct upward for stronger signals:
  • Score ≥ 4.5: +1–2% (strong conviction — let winners run)
  • 1H RSI < 25 (deeply oversold): +1%
  • 4H RSI also oversold (< 35): +1% (double timeframe confirmation)
  • Bullish MACD crossover confirmed: +0.5–1%
  • Funding rate negative (shorts overheated, squeeze setup): +0.5%

Never include a TRADE tag after a [HOLD] or a [SKIP]. Use only plain numbers (no commas, no $ signs).

Always end your response with this exact line:
"⚠ Not financial advice. Crypto is high-risk — only invest what you can afford to lose completely."`;
}

function buildPrompt() {
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

  return `Give me the best crypto trading opportunities RIGHT NOW based on the technical data.\n\nSpecifically:\n1. Top 2-3 [BUY] opportunities — entry price, take profit %, stop loss %, and why\n2. Any coins I should [SELL] or avoid buying\n3. Overall market direction (bull/bear/sideways)\n4. How long do these signals typically take to play out?\n\n${ctx}`;
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

  let coinAmt = price > 0 ? action.amountUsdt / price : null;

  el('tradeConfirmTitle').textContent = `${isBuy ? '🟢 BUY' : '🔴 SELL'} ${coin}`;

  const isO3 = isBuy && action.partialTpPct > 0;

  if (isO3) {
    // ─── Option 3: Partial TP + Trailing Stop ──────────────────────────────
    let amtUsdt = action.amountUsdt;
    let pTpPct = action.partialTpPct;
    let trailPct = action.trailingCallbackPct || 2.5;
    let slPct = action.slPct || 8;

    function calcO3Prices() {
      return {
        ptPrice: price * (1 + pTpPct / 100),
        slPrice: price * (1 - slPct / 100),
      };
    }

    function updateO3Display() {
      const { ptPrice, slPrice } = calcO3Prices();
      const ptEl = document.getElementById('o3_ptPrice');
      const slEl = document.getElementById('o3_slPrice');
      const taEl = document.getElementById('o3_trailAct');
      const cdEl = document.getElementById('o3_coinDisp');
      if (ptEl) ptEl.textContent = fmtCrypto(ptPrice);
      if (slEl) slEl.textContent = fmtCrypto(slPrice);
      if (taEl) taEl.textContent = fmtCrypto(ptPrice);
      if (cdEl && price > 0) cdEl.textContent = ` ≈ ${parseFloat((amtUsdt / price).toFixed(6))} ${coin}`;
    }

    const { ptPrice, slPrice } = calcO3Prices();
    const coinDispInit = price > 0 ? ` ≈ ${parseFloat((amtUsdt / price).toFixed(6))} ${coin}` : '';

    el('tradeConfirmDetails').innerHTML = `
      <div class="o3-plan">
        <table class="trade-detail-table">
          <tr>
            <td>Trade Amount</td>
            <td><input type="number" id="i_amtUsdt" class="o3-amt-input" value="${amtUsdt}" min="1" step="1"> USDT<span id="o3_coinDisp">${coinDispInit}</span></td>
          </tr>
        </table>
        <div class="o3-phase-label">Phase 1 — takes effect immediately on OKX (24/7 active)</div>
        <table class="trade-detail-table">
          <tr><td>Sell 50% when price hits</td><td><b id="o3_ptPrice">${fmtCrypto(ptPrice)}</b> <span class="pos">+${pTpPct}%</span></td></tr>
          <tr><td>Stop Loss (50%)</td><td><b id="o3_slPrice">${fmtCrypto(slPrice)}</b> <span class="neg">−${slPct}%</span></td></tr>
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

    document.getElementById('i_amtUsdt').addEventListener('input', e => {
      const v = parseFloat(e.target.value); if (v > 0) { amtUsdt = v; updateO3Display(); }
    });
    document.getElementById('i_pTpPct').addEventListener('input', e => {
      const v = parseFloat(e.target.value); if (v > 0) { pTpPct = v; updateO3Display(); }
    });
    document.getElementById('i_trailPct').addEventListener('input', e => {
      const v = parseFloat(e.target.value); if (v > 0) trailPct = v;
    });
    document.getElementById('i_slPct').addEventListener('input', e => {
      const v = parseFloat(e.target.value); if (v > 0) { slPct = v; updateO3Display(); }
    });

    const btn = el('tradeConfirmBtn');
    btn.disabled = false;
    btn.textContent = '⚡ Confirm Option 3 Trade';
    btn.onclick = () => executeTrade({ ...action, amountUsdt: amtUsdt, _pTpPct: pTpPct, _trailPct: trailPct, _slPct: slPct }, null);

  } else {
    // ─── Simple market sell ───────────────────────────────────────────────
    el('tradeConfirmDetails').innerHTML = `
      <table class="trade-detail-table">
        <tr><td>Order Type</td><td>Spot Market Sell — executes instantly</td></tr>
        <tr><td>Amount</td><td>${fmtMoney(action.amountUsdt)} USDT${coinAmt ? ` ≈ ${parseFloat(coinAmt.toFixed(6))} ${coin}` : ''}</td></tr>
      </table>`;

    const btn = el('tradeConfirmBtn');
    btn.disabled = false;
    btn.textContent = '⚡ Confirm Sell';
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
      const ptPrice = price * (1 + action._pTpPct / 100);
      const slPrice = price * (1 - action._slPct / 100);
      const baseAlgo = { instId: action.symbol, tdMode: 'cash', side: 'sell' };

      btn.textContent = 'Setting up TP/SL/Trailing…';

      // Order 2: OCO conditional — TP and SL in ONE order so OKX only reserves
      // halfSz balance once (they're mutually exclusive). Separate orders would
      // reserve halfSz each → 3 orders × 50% = 150% of owned coins → rejected.
      // Order 3: Trailing stop on the remaining 50%, activates when TP price is reached.
      const [ocoResult, trailResult] = await Promise.allSettled([
        okxSignedPost('/api/v5/trade/order-algo', {
          ...baseAlgo, ordType: 'conditional', sz: halfSz,
          tpTriggerPx: ptPrice.toFixed(8), tpOrdPx: '-1', tpTriggerPxType: 'last',
          slTriggerPx: slPrice.toFixed(8), slOrdPx: '-1', slTriggerPxType: 'last',
        }),
        okxSignedPost('/api/v5/trade/order-algo', {
          ...baseAlgo, ordType: 'move_order_stop', sz: halfSz,
          activePx: ptPrice.toFixed(8),
          callbackRatio: (action._trailPct / 100).toFixed(4),
        }),
      ]);

      const labels = ['TP/SL (OCO 50%)', 'Trailing Stop (50%)'];
      const passed = [ocoResult, trailResult].filter(r => r.status === 'fulfilled').length;

      if (passed === 2) {
        toast('Option 3 active: TP/SL (OCO) + Trailing Stop set on OKX', 'success', 6000);

        // Both orders share the OCO algoId — monitor uses fill price to tell TP vs SL
        const ocoId   = ocoResult.value?.data?.[0]?.algoId;
        const trailId = trailResult.value?.data?.[0]?.algoId;
        if (ocoId && trailId) {
          await saveOption3Trade({
            id: ocoId,
            symbol: action.symbol,
            entryPrice: price,
            partialTpId: ocoId,
            slId: ocoId,       // same ID — monitor compares fill px to entry to detect which side
            trailingId: trailId,
            amountUsdt: action.amountUsdt,
            szHalf: halfSz,
            partialTpPct: action._pTpPct,
            slPct: action._slPct,
            trailingPct: action._trailPct,
          });
        }
      } else {
        [ocoResult, trailResult].forEach((r, i) => {
          if (r.status === 'rejected') {
            console.error(`[Option3] ${labels[i]} failed:`, r.reason?.message);
            toast(`${labels[i]} could not be set — set manually on OKX. ${r.reason?.message ?? ''}`, 'error', 10000);
          }
        });
        if (passed > 0) toast(`${passed}/2 orders placed — check OKX for details`, 'success', 6000);
      }

    }

    closeModal('tradeConfirmModal');
    // Refresh balance after 2 s
    setTimeout(() => {
      fetchOKXBalance().then(details => {
        const usdt = parseFloat(details.find(d => d.ccy === 'USDT')?.cashBal ?? '0') || 0;
        if (usdt > 0) { showUsdtBalance(usdt); LS.set('lastUsdtBalance', usdt); }
      }).catch(() => { });
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
//  REFRESH
// ═══════════════════════════════════════════════════════════
async function refreshAll() {
  if (state.isRefreshing) return;
  state.isRefreshing = true;
  el('refreshBtn').classList.add('spinning');

  await fetchAllData();
  updateSignalTimes();
  renderScanner();
  await checkSignalAlerts();

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
function loadScannerSymbols() {
  const saved = LS.get('scanner', null);
  if (!saved) {
    state.scannerSymbols = [...CONFIG.DEFAULT_SCANNER];
  } else {
    // Merge any new coins from DEFAULT_SCANNER that aren't already saved
    const merged = [...saved];
    for (const sym of CONFIG.DEFAULT_SCANNER) {
      if (!merged.includes(sym)) merged.push(sym);
    }
    state.scannerSymbols = merged;
  }
  saveScannerSymbols();
}
function saveScannerSymbols() { LS.set('scanner', state.scannerSymbols); }

// ═══════════════════════════════════════════════════════════
//  EXPORT / IMPORT
// ═══════════════════════════════════════════════════════════
function exportData() {
  const blob = new Blob([JSON.stringify({ scanner: state.scannerSymbols }, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement('a'), { href: url, download: `trading-scanner-${new Date().toISOString().slice(0, 10)}.json` });
  a.click();
  URL.revokeObjectURL(url);
  toast('Exported', 'success');
}

function importData(file) {
  const reader = new FileReader();
  reader.onload = e => {
    try {
      const data = JSON.parse(e.target.result);
      if (data.scanner) { state.scannerSymbols = data.scanner; saveScannerSymbols(); }
      toast('Imported successfully', 'success');
      closeModal('settingsModal');
      refreshAll();
    } catch { toast('Invalid JSON file', 'error'); }
  };
  reader.readAsText(file);
}

function clearAllData() {
  if (!confirm('Reset all data and settings? This cannot be undone.')) return;
  localStorage.clear();
  state.scannerSymbols = [...CONFIG.DEFAULT_SCANNER];
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

  // Scanner
  el('scannerSortSelect').addEventListener('change', () => { state.scannerSort = el('scannerSortSelect').value; renderScanner(); });

  document.querySelectorAll('.filter-chip').forEach(chip => {
    chip.addEventListener('mouseenter', () => {
      chip.classList.add('active');
      state.scannerFilter = chip.dataset.filter;
      renderScanner();
    });
    chip.addEventListener('mouseleave', () => {
      chip.classList.remove('active');
      state.scannerFilter = 'all';
      renderScanner();
    });
  });

  // News
  el('newsSearchBtn').addEventListener('click', () => refreshNews(el('newsTopicInput').value.trim()));
  el('newsTopicInput').addEventListener('keydown', e => { if (e.key === 'Enter') el('newsSearchBtn').click(); });

  // AI
  el('aiAnalyzeBtn').addEventListener('click', runAiAnalysis);

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

  // Pause auto-refresh when the tab is hidden — saves API calls and prevents
  // duplicate Telegram alerts (GitHub Actions already covers background monitoring).
  // Resume immediately with a fresh fetch when the tab becomes visible again.
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      clearInterval(state.refreshTimer);
      clearInterval(state.newsTimer);
    } else {
      refreshAll();
      restartAutoRefresh();
    }
  });
}

// ═══════════════════════════════════════════════════════════
//  INIT
// ═══════════════════════════════════════════════════════════
async function loadAppData() {
  // Restore last known USDT balance immediately (before first network call)
  const lastBal = LS.get('lastUsdtBalance', 0);
  if (lastBal > 0) showUsdtBalance(lastBal);

  renderScanner();
  await fetchAllData();
  updateSignalTimes();
  renderScanner();
  await checkSignalAlerts();
  refreshNews();
  restartAutoRefresh();
  // Auto-sync OKX balance if credentials are available
  if (CONFIG.OKX_API_KEY && CONFIG.OKX_SECRET_KEY && CONFIG.OKX_PASSPHRASE) {
    fetchOKXBalance().then(details => {
      const usdt = parseFloat(details.find(d => d.ccy === 'USDT')?.cashBal ?? '0') || 0;
      if (usdt > 0) { showUsdtBalance(usdt); LS.set('lastUsdtBalance', usdt); }
    }).catch(() => { });
  }
}

async function init() {
  loadSettings();
  state.notifiedSignals = LS.get('notifiedSignals', {});
  loadScannerSymbols();
  wireEvents();
  startAgeTicker();
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('./sw.js').catch(() => { });

  // If Supabase is configured, try auto-unlock from session first
  if (isSupabaseConfigured()) {
    const saved = sessionStorage.getItem('sp');
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
        sessionStorage.removeItem('sp');
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
