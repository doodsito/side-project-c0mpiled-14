-- Demo user for the MCP server (connector link .../mcp/demo). Idempotent.
-- Run: npx supabase db query --linked -f supabase/seed_demo.sql
-- Replace the phone with the number that receives the demo texts (verified on the Twilio trial account).

with u as (
  insert into public.users (phone, connector_key, weekly_budget)
  values ('+15555550100', 'demo', 75)
  on conflict (connector_key) do update set phone = excluded.phone
  returning id
)
insert into public.envelopes (user_id, category, weekly_limit, auto_approve_under)
select id, 'Software', 75, 10 from u
on conflict (user_id, lower(category)) do update
  set weekly_limit = excluded.weekly_limit, auto_approve_under = excluded.auto_approve_under;
