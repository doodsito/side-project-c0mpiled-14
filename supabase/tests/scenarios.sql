-- Schema guarantees + Data API lockdown. Leaves nothing behind: one transaction rolled back at the end.
-- Run: npx supabase db query --linked -f supabase/tests/scenarios.sql
-- Success = a single row { "result": "all scenarios passed" }. Any failure raises with the failing assertion.

begin;

do $$
declare
  u uuid;
  e uuid;
  r uuid;
begin
  insert into public.users (phone, connector_key, weekly_budget)
  values ('+15550000001', 'test_key_1', 100) returning id into u;
  insert into public.envelopes (user_id, category, weekly_limit, auto_approve_under)
  values (u, 'Software', 75, 10) returning id into e;

  -- Categories are unique per user, case-insensitive
  begin
    insert into public.envelopes (user_id, category, weekly_limit, auto_approve_under) values (u, 'software', 1, 1);
    raise exception 'duplicate category should fail';
  exception when unique_violation then null;
  end;

  -- One pending request per user
  insert into public.requests (user_id, envelope_id, amount, merchant) values (u, e, 29, 'Notion') returning id into r;
  begin
    insert into public.requests (user_id, envelope_id, amount, merchant) values (u, e, 5, 'Other');
    raise exception 'second pending should fail';
  exception when unique_violation then null;
  end;
  update public.requests set status = 'approved', decided_at = now() where id = r and status = 'pending';
  insert into public.requests (user_id, envelope_id, amount, merchant) values (u, e, 5, 'Other');
  assert (select amount from public.requests where id = r) = 29.00, 'amount stored in dollars';

  -- Invalid data
  begin
    insert into public.requests (user_id, envelope_id, amount, status) values (u, e, 0, 'approved');
    raise exception 'zero amount should fail';
  exception when check_violation then null;
  end;
  begin
    insert into public.users (phone, connector_key, weekly_budget) values ('5550000002', 'test_key_2', 1);
    raise exception 'non E.164 phone should fail';
  exception when check_violation then null;
  end;

  -- Publishable key roles can't reach the tables
  execute 'set local role anon';
  begin
    perform count(*) from public.users;
    raise exception 'anon should not read users';
  exception when insufficient_privilege then null;
  end;
  execute 'reset role';
  execute 'set local role authenticated';
  begin
    perform count(*) from public.requests;
    raise exception 'authenticated should not read requests';
  exception when insufficient_privilege then null;
  end;
  execute 'reset role';

  -- Server side (secret key) can
  execute 'set local role service_role';
  assert (select count(*) from public.requests where user_id = u) = 2, 'service_role reads requests';
  execute 'reset role';

  -- Deleting a user removes their data
  delete from public.users where id = u;
  assert not exists (select 1 from public.requests where user_id = u), 'cascade delete';
end;
$$;

select 'all scenarios passed' as result;

rollback;
