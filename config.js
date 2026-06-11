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

  // Supabase — encrypted cloud settings storage (pre-configured, no manual setup needed)
  SUPABASE_URL: 'https://trbfhtopkcupzeqmrnom.supabase.co',
  SUPABASE_ANON_KEY: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRyYmZodG9wa2N1cHplcW1ybm9tIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODExNDI1NDYsImV4cCI6MjA5NjcxODU0Nn0.6XKKIJIotc4lRVL_akt7P63woJiB8NyOVaUotQmmpHQ',

  // CryptoCompare News — free, no key needed
  CRYPTOCOMPARE_URL: 'https://min-api.cryptocompare.com/data/v2/news/?lang=EN&sortOrder=popular',

  // Refresh timing
  AUTO_REFRESH_INTERVAL: 60_000,       // 1 minute (crypto moves fast)
  NEWS_REFRESH_INTERVAL: 10 * 60_000,  // 10 minutes
  CANDLE_BAR: '1H',                    // 1-hour candles for indicators
  CANDLE_LIMIT: 100,
  MAX_NEWS_ARTICLES: 8,

  // Your holdings — leave empty, add via the + Add button
  DEFAULT_PORTFOLIO: [],

  // Default coins to watch on OKX
  DEFAULT_SCANNER: [
    'BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'BNB-USDT', 'XRP-USDT',
    'DOGE-USDT', 'ADA-USDT', 'AVAX-USDT', 'MATIC-USDT', 'DOT-USDT',
  ],

  RISK_PROFILE: 'moderate',   // conservative | moderate | aggressive
  TRADING_CAPITAL: 0,         // total USDT you trade with — used for position sizing
  CURRENCY_SYMBOL: '$',
};
