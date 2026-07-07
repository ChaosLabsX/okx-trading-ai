const CONFIG = {
  // Claude AI (required for AI Advisor)
  CLAUDE_API_KEY: '',
  CLAUDE_MODEL: 'claude-sonnet-4-6',
  CLAUDE_API_URL: 'https://api.anthropic.com/v1/messages',

  // OKX Public Market API — no key needed
  OKX_BASE: 'https://www.okx.com',

  // OKX Private API — for reading your account balance (use read-only key)
  OKX_API_KEY: '',
  OKX_SECRET_KEY: '',
  OKX_PASSPHRASE: '',

  // Telegram Bot — for STRONG BUY / STRONG SELL alerts
  TELEGRAM_BOT_TOKEN: '',
  TELEGRAM_CHAT_ID: '',

  // CryptoCompare News API — reliable, coin-tagged news (free read-only key,
  // scope: price/polling endpoints only — safe to ship like the Supabase anon key)
  CRYPTOCOMPARE_API_KEY: '9b260f1d70267786f07b9fc29fc785dae1f187863c7ae5466ede5e8a6f36b4a9',

  // CryptoPanic — community bullish/bearish votes for news sentiment.
  // NOTE (Jul 2026): CryptoPanic's API is now paid (~$50/week) — not worth it.
  // Leave '' (keyword-based sentiment is used instead; trading is unaffected —
  // the AI reads raw headlines itself). If a key ever appears here, voted
  // sentiment activates automatically.
  CRYPTOPANIC_API_KEY: '',

  // Supabase — encrypted cloud settings storage (pre-configured, no manual setup needed)
  SUPABASE_URL: 'https://trbfhtopkcupzeqmrnom.supabase.co',
  SUPABASE_ANON_KEY: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRyYmZodG9wa2N1cHplcW1ybm9tIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODExNDI1NDYsImV4cCI6MjA5NjcxODU0Nn0.6XKKIJIotc4lRVL_akt7P63woJiB8NyOVaUotQmmpHQ',

  // Refresh timing
  AUTO_REFRESH_INTERVAL: 60_000,       // 1 minute (crypto moves fast)
  NEWS_REFRESH_INTERVAL: 10 * 60_000,  // 10 minutes
  CANDLE_BAR: '1H',                    // 1-hour candles for indicators
  CANDLE_LIMIT: 100,
  MAX_NEWS_ARTICLES: 8,

  // Your holdings — leave empty, add via the + Add button
  DEFAULT_PORTFOLIO: [],

  // Default coins to watch on OKX — audited 2026-07-07 against live OKX data.
  // Keep in sync with SYMBOLS in signal_checker.py (the worker's trade universe).
  DEFAULT_SCANNER: [
    // Majors
    'BTC-USDT',  'ETH-USDT',  'BNB-USDT',  'SOL-USDT',  'XRP-USDT',
    'ADA-USDT',  'DOGE-USDT', 'TRX-USDT',  'LTC-USDT',  'BCH-USDT',
    'XLM-USDT',
    // L1 / L2 / infrastructure
    'AVAX-USDT', 'SUI-USDT',  'NEAR-USDT', 'APT-USDT',  'TIA-USDT',
    'SEI-USDT',  'OP-USDT',   'ARB-USDT',  'DOT-USDT',  'HBAR-USDT',
    'POL-USDT',  'MON-USDT',  'HYPE-USDT', 'ZEC-USDT',
    // DeFi / AI
    'LINK-USDT', 'UNI-USDT',  'AAVE-USDT', 'LDO-USDT',  'ENA-USDT',
    'ONDO-USDT', 'JUP-USDT',  'INJ-USDT',  'FET-USDT',  'TAO-USDT',
    'WLD-USDT',
    // Memes (high volume + volatility)
    'PEPE-USDT', 'BONK-USDT',
  ],

  // Fixed values — intentionally removed from the Settings UI (single-user app):
  // risk profile is always aggressive, market data always refreshes every 1 minute.
  RISK_PROFILE: 'aggressive',
  TRADING_CAPITAL: 0,         // total USDT you trade with — used for position sizing
  CURRENCY_SYMBOL: '$',
};
