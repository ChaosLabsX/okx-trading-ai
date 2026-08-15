const CONFIG = {
  // Claude AI (required for AI Advisor)
  CLAUDE_API_KEY: '',
  // Same Opus as signal_checker.py — one model everywhere. Static page, so there
  // are no env vars: this line IS the config, and changing it needs a commit + push
  // to reach the live site. Note Opus 5 thinks by default, so a click here takes
  // noticeably longer than it did on Sonnet (see max_tokens in runAiAnalysis).
  CLAUDE_MODEL: 'claude-opus-5',
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

  // Score at which a coin is labelled STRONG BUY in the scanner. MUST match
  // STRONG_BUY_SCORE in signal_checker.py — that constant is what actually decides
  // whether the worker trades, and a dashboard showing "BUY" for a coin the bot
  // just bought (or "STRONG BUY" for one it ignored) is the confusing half of a
  // drifted pair. Lowered 5.0 → 4.5 on 2026-08-15 alongside the worker; the
  // reasoning lives in signal_checker.py next to that constant.
  //
  // Matching this bar is necessary but not sufficient — the two generateSignal
  // implementations must also agree on the SCORE compared against it. They did not
  // until 2026-08-15 (app.js scored the volume term differently and in the wrong
  // position, so 80 of 720 grid cases got a different LABEL). Both sides were
  // aligned to the worker's rules and verified across 8,400 cases: 0 mismatches on
  // score, label and reason strings. Change one side, change the other.
  STRONG_BUY_SCORE: 4.5,

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
