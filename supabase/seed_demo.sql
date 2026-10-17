-- Demo user: Software envelope $75/week, auto-approve under $10, weekly allowance $200. Idempotent.
-- Run: npx supabase db query --linked -f supabase/seed_demo.sql
-- Replace the phone (digits only, as auth.users stores it) with the number that receives the demo texts.
-- The connector key is never committed: set it with
--   update public.profiles set connector_key_hash = private.hash_key('<key>') where id = '00000000-0000-4000-8000-00000000de30';

insert into auth.users (
  instance_id, id, aud, role, phone, phone_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at,
  confirmation_token, recovery_token, email_change_token_new, email_change,
  phone_change, phone_change_token, email_change_token_current, reauthentication_token
)
values (
  '00000000-0000-0000-0000-000000000000', '00000000-0000-4000-8000-00000000de30',
  'authenticated', 'authenticated', '15555550100', now(),
  '{"provider": "phone", "providers": ["phone"]}', '{}', now(), now(),
  '', '', '', '', '', '', '', ''
)
on conflict (id) do update set phone = excluded.phone, phone_confirmed_at = now();

update public.envelopes set weekly_limit_cents = 7500, auto_approve_under_cents = 1000
where user_id = '00000000-0000-4000-8000-00000000de30' and category = 'software';

update public.profiles set weekly_allowance_cents = 20000
where id = '00000000-0000-4000-8000-00000000de30';
