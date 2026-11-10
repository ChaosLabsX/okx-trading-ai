-- Paper trading, the bot's status row, and a journal for EVERY rejected signal.
-- Applied 2026-11-10. Safe to run again: every statement is "if not exists" / idempotent.
-- Supabase -> the OKX project -> SQL Editor -> paste -> Run.

-- 1. Paper trades: strategies that run on live prices with simulated fills. Real money
--    lives in option3_trades and nowhere else; the two are never mixed in one table.
create table if not exists public.paper_trades (
  id            bigint generated always as identity primary key,
  strategy      text        not null,                 -- dip | flush | trend | ...
  symbol        text        not null,
  kind          text        not null default 'option3', -- exit model: option3 | hold | rule
  opened_at     timestamptz not null default now(),
  entry_price   numeric     not null,
  amount_usdt   numeric     not null,
  params        jsonb       not null default '{}',     -- tp/sl/trail %, hold hours ...
  state         jsonb       not null default '{}',     -- phase, peak, break-even flag ...
  entry_context jsonb       not null default '{}',
  closed_at     timestamptz,
  exit_price    numeric,
  exit_reason   text,
  net_pnl_usdt  numeric,
  net_pct       numeric,                               -- after fees and slippage, % of the stake
  mae_pct       numeric,                               -- worst point reached while open
  mfe_pct       numeric,                               -- best point reached while open
  followup      jsonb,
  followup_at   timestamptz
);
create index if not exists paper_trades_open_idx   on public.paper_trades (strategy) where closed_at is null;
create index if not exists paper_trades_closed_idx on public.paper_trades (closed_at desc);

-- 2. One row the worker rewrites every few minutes: is it alive, what does the market
--    look like, and how many signals each gate stopped (the "why no trade" funnel).
create table if not exists public.bot_status (
  id         text primary key,
  updated_at timestamptz not null default now(),
  data       jsonb       not null default '{}'
);

-- 3. The skip journal used to hold only what the AI declined. `gate` says who said no
--    (ai | regime | max_open | breaker | correlation | funding | ...), so the filters can
--    be graded the same way the AI is: did saying no save money or cost it?
alter table public.skipped_setups add column if not exists gate     text not null default 'ai';
alter table public.skipped_setups add column if not exists strategy text not null default 'dip';
create index if not exists skipped_setups_created_idx  on public.skipped_setups (created_at desc);
create index if not exists skipped_setups_ungraded_idx on public.skipped_setups (created_at) where followup_at is null;

-- 4. Which strategy placed a live trade (everything so far is the dip strategy).
alter table public.option3_trades add column if not exists strategy text not null default 'dip';
create index if not exists option3_trades_closed_idx on public.option3_trades (closed_at desc);

-- 5. Who may do what. New tables in `public` are born readable AND writable by the public
--    key (Supabase default privileges), so take everything away first, then give back
--    read-only. The worker's private key gets read/insert/update, never delete.
revoke all on public.paper_trades, public.bot_status from anon, authenticated;
grant select on public.paper_trades, public.bot_status, public.skipped_setups to anon, authenticated;
grant select, insert, update on public.paper_trades, public.bot_status to service_role;
grant usage, select on all sequences in schema public to service_role;
-- Leftovers the 2026-11-03 lock-down did not cover. None is reachable through the REST API,
-- but a public key has no business holding them.
revoke truncate, references, trigger on all tables in schema public from anon, authenticated;
