-- Approval rules + RLS scenarios. Leaves nothing behind: everything runs in one transaction rolled back at the end.
-- Run: npx supabase db query --linked -f supabase/tests/scenarios.sql
-- Success = a single row { "result": "all scenarios passed" }. Any failure raises with the failing assertion.

begin;

do $$
declare
  a uuid := gen_random_uuid();
  b uuid := gen_random_uuid();
  c uuid := gen_random_uuid();
  a_phone text := '+15550000001';
  key_a text;
  key_b text;
  r jsonb;
  pending_id uuid;
  n integer;
  err text;
begin
  -- Setup (as postgres): A and B confirmed, C unconfirmed
  insert into auth.users (id, aud, role, phone, phone_confirmed_at)
  values (a, 'authenticated', 'authenticated', '15550000001', now()),
         (b, 'authenticated', 'authenticated', '15550000002', now()),
         (c, 'authenticated', 'authenticated', '15550000003', null);

  assert (select phone from public.profiles where id = a) = a_phone, 'signup syncs confirmed phone in E.164';
  assert (select phone from public.profiles where id = c) is null, 'unconfirmed phone not synced';
  assert (select count(*) from public.envelopes where user_id = a) = 4, 'signup creates 4 envelopes';

  update auth.users set phone_confirmed_at = now() where id = c;
  assert (select phone from public.profiles where id = c) = '+15550000003', 'phone synced on confirmation';

  -- Front (authenticated as A): set budget, rotate key
  execute 'set local role authenticated';
  perform set_config('request.jwt.claims', json_build_object('sub', a, 'role', 'authenticated')::text, true);

  update public.profiles set weekly_allowance_cents = 20000 where id = a;
  update public.envelopes set weekly_limit_cents = 10400, auto_approve_under_cents = 2000
    where user_id = a and category = 'software';
  update public.envelopes set weekly_limit_cents = 50000 where user_id = a and category = 'travel';
  key_a := public.rotate_connector_key();
  assert key_a like 'wk_%', 'rotate returns key';

  begin
    update public.profiles set phone = '+15559999999' where id = a;
    raise exception 'phone should not be editable';
  exception when insufficient_privilege then null;
  end;

  begin
    perform public.request_spend(key_a, 100, 'food', 'x');
    raise exception 'authenticated should not call request_spend';
  exception when insufficient_privilege then null;
  end;

  -- B cannot see or touch A's data
  perform set_config('request.jwt.claims', json_build_object('sub', b, 'role', 'authenticated')::text, true);
  key_b := public.rotate_connector_key();
  update public.envelopes set weekly_limit_cents = 1 where user_id = a;
  get diagnostics n = row_count;
  assert n = 0, 'B cannot update A envelopes';
  assert (select count(*) from public.profiles) = 1, 'B sees only own profile';

  -- anon sees nothing
  execute 'reset role';
  execute 'set local role anon';
  begin
    perform count(*) from public.envelopes;
    raise exception 'anon should not read envelopes';
  exception when insufficient_privilege then null;
  end;

  -- MCP server / Twilio webhook (service_role)
  execute 'reset role';
  insert into public.spend_requests (user_id, envelope_id, amount_cents, merchant, status, reason, decided_by, decided_at, created_at)
  select a, id, 9999, 'old', 'approved', 'within_auto', 'auto', now() - interval '8 days', now() - interval '8 days'
  from public.envelopes where user_id = a and category = 'software';
  execute 'set local role service_role';

  begin
    perform public.request_spend('wk_bad', 100, 'food', 'x');
    raise exception 'bad key should fail';
  exception when others then
    err := sqlerrm;
    assert err = 'invalid_connector_key', 'bad key error: ' || err;
  end;

  -- Under auto-approve + in budget -> approved
  r := public.request_spend(key_a, 1500, 'Software', 'GitHub');
  assert r->>'decision' = 'approved' and r->>'reason' = 'within_auto' and r->>'decided_by' = 'auto', 'auto approve: ' || r::text;

  -- In budget, over auto-approve -> pending
  r := public.request_spend(key_a, 2900, 'software', 'Figma', 'Pro plan');
  assert r->>'decision' = 'pending' and r->>'reason' = 'over_auto', 'over auto: ' || r::text;
  assert r->>'phone' = a_phone, 'pending returns phone for the text';
  pending_id := (r->>'request_id')::uuid;

  -- One pending at a time
  r := public.request_spend(key_a, 100, 'food', 'x');
  assert r->>'decision' = 'blocked' and (r->>'pending_request_id')::uuid = pending_id, 'blocked: ' || r::text;

  -- Bad reply keeps it pending, Y approves and reserves
  r := public.resolve_reply(a_phone, 'maybe');
  assert r->>'result' = 'invalid_reply' and r->>'status' = 'pending', 'invalid reply: ' || r::text;
  r := public.resolve_reply('15550000001', ' y ');
  assert r->>'result' = 'approved' and r->>'decided_by' = 'sms', 'Y approves: ' || r::text;
  assert (r->>'envelope_remaining_cents')::int = 10400 - 1500 - 2900, 'approved is reserved, 8-day-old excluded: ' || r::text;

  -- Over envelope -> pending with over_by, N declines
  r := public.request_spend(key_a, 7000, 'software', 'Adobe');
  assert r->>'decision' = 'pending' and r->>'reason' = 'over_envelope' and (r->>'over_by_cents')::int = 1000, 'over envelope: ' || r::text;
  r := public.resolve_reply(a_phone, 'no');
  assert r->>'result' = 'declined', 'N declines: ' || r::text;

  -- No reply in 10 min -> expired, late Y does nothing
  r := public.request_spend(key_a, 100, 'food', 'Pizza');
  pending_id := (r->>'request_id')::uuid;
  execute 'reset role';
  update public.spend_requests set expires_at = now() - interval '1 second' where id = pending_id;
  -- now() is frozen inside the transaction: age the other requests so the expired one is the latest
  update public.spend_requests set created_at = now() - interval '1 minute' where user_id = a and id <> pending_id and created_at = now();
  execute 'set local role service_role';
  r := public.get_status(key_a, pending_id);
  assert r->>'status' = 'expired' and r->>'decided_by' = 'timeout', 'expired: ' || r::text;
  r := public.resolve_reply(a_phone, 'Y');
  assert r->>'result' = 'expired', 'late reply: ' || r::text;
  r := public.resolve_reply('+15550000002', 'Y');
  assert r->>'result' = 'no_pending', 'no pending: ' || r::text;
  r := public.resolve_reply('+15551111111', 'Y');
  assert r->>'result' = 'unknown_phone', 'unknown phone: ' || r::text;

  begin
    perform public.request_spend(key_a, 100, 'groceries', 'x');
    raise exception 'invalid category should fail';
  exception when invalid_parameter_value then null;
  end;

  -- Over total (envelope fine) -> pending over_total
  update public.profiles set weekly_allowance_cents = 5000 where id = a;
  r := public.request_spend(key_a, 900, 'travel', 'Uber');
  assert r->>'decision' = 'pending' and r->>'reason' = 'over_total' and (r->>'over_by_cents')::int = 300, 'over total: ' || r::text;

  r := public.check_budget(key_a);
  assert (r->>'total_spent_cents')::int = 4400 and (r->>'has_pending')::boolean, 'check_budget: ' || r::text;
  assert jsonb_array_length(r->'envelopes') = 4, 'check_budget envelopes';

  -- Keys are per user
  begin
    perform public.get_status(key_b, pending_id);
    raise exception 'B should not read A request';
  exception when no_data_found then null;
  end;

  execute 'reset role';
end;
$$;

select 'all scenarios passed' as result;

rollback;
