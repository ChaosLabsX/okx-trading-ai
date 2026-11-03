-- STEP 2 of 2 - run ONLY after `db_check.py` on the VPS prints "PASS - worker key"
-- with the service_role key in C:\OKXAI\.env.
-- Run it before that and the worker loses the ability to record and update its own
-- trades (it would still be using the public key this script takes rights from).
--
-- Supabase -> your OKX project -> SQL Editor -> paste -> Run.
--
-- The public key (anon) ships in config.js, on GitHub and on the website, so anything
-- it can do, anyone on the internet can do. Until 2026-11-03 that included editing
-- and deleting trade rows the worker acts on. After this it can:
--   - read trades          (the dashboard's performance panel)
--   - read/save settings   (the dashboard's unlock and "Save All to Cloud"; the data
--                           is encrypted in the browser with your password)
-- and nothing else. To undo: grant the same rights back with GRANT ... TO anon.

revoke insert, update, delete on public.option3_trades from anon, authenticated;
revoke all                    on public.skipped_setups from anon, authenticated;
revoke insert, update, delete on public.learned_rules  from anon, authenticated;
revoke delete                 on public.app_settings   from anon, authenticated;
