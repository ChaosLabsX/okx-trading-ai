-- STEP 1 of 2 - safe to run at any time, as often as you like.
-- Supabase -> your OKX project -> SQL Editor -> paste -> Run.
--
-- Lets the PRIVATE service_role key (the worker's key, in C:\OKXAI\.env on the VPS)
-- use every table the worker writes. Nothing changes for the public key here.
--
-- Why it is needed: skipped_setups was created without permissions for the API roles,
-- so even a correct key could not write the skip journal until these grants exist.

grant select, insert, update on public.option3_trades to service_role;
grant select, insert, update on public.skipped_setups to service_role;
grant select, insert, update on public.learned_rules  to service_role;
grant usage, select on all sequences in schema public to service_role;
