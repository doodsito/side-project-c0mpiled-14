-- Back to the RPC design of 20261017205602_init_schema: the team (docs/TEAM-CONTRACT.md, mcp-server/db.py)
-- built against it, so the contract-shaped tables of 20261017211932 are dropped. They only held the demo user.
-- Money is integer cents. Budgets = approved requests of the last 7 days (no reset job).

drop table public.requests;
drop table public.envelopes;
drop table public.users;

create schema if not exists private;
revoke all on schema private from public, anon, authenticated;
grant usage on schema private to service_role;

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

create table public.profiles (
  id uuid primary key references auth.users on delete cascade,
  -- E.164, synced from auth.users once the phone is confirmed
  phone text unique check (phone ~ '^\+[1-9][0-9]{6,14}$'),
  weekly_allowance_cents integer not null default 0 check (weekly_allowance_cents >= 0),
  connector_key_hash text unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.envelopes (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users on delete cascade,
  category text not null check (category in ('food', 'travel', 'shopping', 'software')),
  weekly_limit_cents integer not null default 0 check (weekly_limit_cents >= 0),
  -- requests strictly below this amount (and within budget) are approved without a text
  auto_approve_under_cents integer not null default 0 check (auto_approve_under_cents >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, category)
);

create table public.spend_requests (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users on delete cascade,
  envelope_id bigint not null references public.envelopes on delete cascade,
  amount_cents integer not null check (amount_cents > 0),
  merchant text not null check (length(merchant) between 1 and 200),
  description text check (length(description) <= 1000),
  status text not null default 'pending'
    check (status in ('pending', 'approved', 'declined', 'expired')),
  reason text not null
    check (reason in ('within_auto', 'over_auto', 'over_envelope', 'over_total')),
  decided_by text check (decided_by in ('auto', 'sms', 'timeout')),
  over_by_cents integer not null default 0 check (over_by_cents >= 0),
  expires_at timestamptz not null default now() + interval '10 minutes',
  decided_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check ((status = 'pending') = (decided_by is null))
);

-- One pending request per user at a time
create unique index spend_requests_one_pending_idx on public.spend_requests (user_id)
  where status = 'pending';
-- RLS, history, total budget math
create index spend_requests_user_id_created_at_idx on public.spend_requests (user_id, created_at desc);
-- Envelope budget math
create index spend_requests_envelope_approved_idx on public.spend_requests (envelope_id, created_at)
  where status = 'approved';

-- ---------------------------------------------------------------------------
-- updated_at
-- ---------------------------------------------------------------------------

create function private.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger profiles_set_updated_at before update on public.profiles
  for each row execute function private.set_updated_at();
create trigger envelopes_set_updated_at before update on public.envelopes
  for each row execute function private.set_updated_at();
create trigger spend_requests_set_updated_at before update on public.spend_requests
  for each row execute function private.set_updated_at();

-- ---------------------------------------------------------------------------
-- Signup: profile + the 4 envelopes; keep profiles.phone in sync with the confirmed phone
-- ---------------------------------------------------------------------------

create function private.handle_auth_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_phone text := case
    when new.phone_confirmed_at is not null and coalesce(new.phone, '') <> ''
      then '+' || ltrim(new.phone, '+')
  end;
begin
  if tg_op = 'INSERT' then
    insert into public.profiles (id, phone) values (new.id, v_phone);
    insert into public.envelopes (user_id, category)
    select new.id, c from unnest(array['food', 'travel', 'shopping', 'software']) as c;
  else
    update public.profiles set phone = v_phone where id = new.id and phone is distinct from v_phone;
  end if;
  return new;
end;
$$;

revoke all on function private.handle_auth_user() from public, anon, authenticated;

create trigger on_auth_user_created after insert on auth.users
  for each row execute function private.handle_auth_user();
create trigger on_auth_user_phone_updated after update of phone, phone_confirmed_at on auth.users
  for each row execute function private.handle_auth_user();

-- ---------------------------------------------------------------------------
-- RLS and grants (front = authenticated, MCP server + Twilio webhook = service_role)
-- ---------------------------------------------------------------------------

alter table public.profiles enable row level security;
alter table public.envelopes enable row level security;
alter table public.spend_requests enable row level security;

revoke all on public.profiles, public.envelopes, public.spend_requests from anon, authenticated;
grant all on public.profiles, public.envelopes, public.spend_requests to service_role;

grant select on public.profiles to authenticated;
grant update (weekly_allowance_cents) on public.profiles to authenticated;
grant select on public.envelopes to authenticated;
grant update (weekly_limit_cents, auto_approve_under_cents) on public.envelopes to authenticated;
grant select on public.spend_requests to authenticated;

create policy "Users read own profile" on public.profiles for select
  to authenticated using ((select auth.uid()) = id);
create policy "Users update own profile" on public.profiles for update
  to authenticated using ((select auth.uid()) = id) with check ((select auth.uid()) = id);

create policy "Users read own envelopes" on public.envelopes for select
  to authenticated using ((select auth.uid()) = user_id);
create policy "Users update own envelopes" on public.envelopes for update
  to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

create policy "Users read own spend requests" on public.spend_requests for select
  to authenticated using ((select auth.uid()) = user_id);

-- ---------------------------------------------------------------------------
-- Internal helpers (service_role only)
-- ---------------------------------------------------------------------------

create function private.hash_key(p_key text)
returns text
language sql
immutable
set search_path = ''
as $$
  select encode(extensions.digest(p_key, 'sha256'), 'hex');
$$;

-- Resolves a connector key to its user and locks the profile row, so budget checks are serialized per user.
create function private.lock_user_by_key(p_key text)
returns uuid
language plpgsql
set search_path = ''
as $$
declare
  v_user_id uuid;
begin
  select id into v_user_id
  from public.profiles
  where connector_key_hash = private.hash_key(p_key)
  for update;

  if v_user_id is null then
    raise exception 'invalid_connector_key' using errcode = '28000';
  end if;
  return v_user_id;
end;
$$;

-- Lazy 10-minute expiry
create function private.expire_pending(p_user_id uuid)
returns void
language sql
set search_path = ''
as $$
  update public.spend_requests
  set status = 'expired', decided_by = 'timeout', decided_at = expires_at
  where user_id = p_user_id and status = 'pending' and expires_at <= now();
$$;

create function private.envelope_spent_cents(p_envelope_id bigint)
returns integer
language sql
stable
set search_path = ''
as $$
  select coalesce(sum(amount_cents), 0)::integer
  from public.spend_requests
  where envelope_id = p_envelope_id and status = 'approved' and created_at > now() - interval '7 days';
$$;

create function private.total_spent_cents(p_user_id uuid)
returns integer
language sql
stable
set search_path = ''
as $$
  select coalesce(sum(amount_cents), 0)::integer
  from public.spend_requests
  where user_id = p_user_id and status = 'approved' and created_at > now() - interval '7 days';
$$;

create function private.request_json(p_request_id uuid)
returns jsonb
language sql
stable
set search_path = ''
as $$
  select jsonb_build_object(
    'request_id', r.id,
    'status', r.status,
    'reason', r.reason,
    'decided_by', r.decided_by,
    'amount_cents', r.amount_cents,
    'category', e.category,
    'merchant', r.merchant,
    'description', r.description,
    'over_by_cents', r.over_by_cents,
    'envelope_remaining_cents', e.weekly_limit_cents - private.envelope_spent_cents(e.id),
    'total_remaining_cents', p.weekly_allowance_cents - private.total_spent_cents(r.user_id),
    'phone', p.phone,
    'expires_at', r.expires_at,
    'decided_at', r.decided_at,
    'created_at', r.created_at
  )
  from public.spend_requests r
  join public.envelopes e on e.id = r.envelope_id
  join public.profiles p on p.id = r.user_id
  where r.id = p_request_id;
$$;

revoke all on all functions in schema private from public, anon, authenticated;
grant execute on all functions in schema private to service_role;

-- ---------------------------------------------------------------------------
-- Front RPC (authenticated)
-- ---------------------------------------------------------------------------

-- Returns the new connector key once; only its hash is stored. Invalidates the previous key.
create function public.rotate_connector_key()
returns text
language plpgsql
set search_path = ''
as $$
declare
  v_key text := 'wk_' || encode(extensions.gen_random_bytes(24), 'hex');
begin
  update public.profiles
  set connector_key_hash = encode(extensions.digest(v_key, 'sha256'), 'hex')
  where id = (select auth.uid());

  if not found then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  return v_key;
end;
$$;

grant update (connector_key_hash) on public.profiles to authenticated;
revoke all on function public.rotate_connector_key() from public, anon;
grant execute on function public.rotate_connector_key() to authenticated;

-- ---------------------------------------------------------------------------
-- MCP server + Twilio webhook RPCs (service_role)
-- ---------------------------------------------------------------------------

create function public.check_budget(p_key text)
returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  v_user_id uuid := private.lock_user_by_key(p_key);
begin
  perform private.expire_pending(v_user_id);

  return (
    select jsonb_build_object(
      'weekly_allowance_cents', p.weekly_allowance_cents,
      'total_spent_cents', private.total_spent_cents(p.id),
      'total_remaining_cents', p.weekly_allowance_cents - private.total_spent_cents(p.id),
      'has_pending', exists (
        select 1 from public.spend_requests where user_id = p.id and status = 'pending'
      ),
      'envelopes', (
        select jsonb_agg(jsonb_build_object(
          'category', e.category,
          'weekly_limit_cents', e.weekly_limit_cents,
          'auto_approve_under_cents', e.auto_approve_under_cents,
          'spent_cents', private.envelope_spent_cents(e.id),
          'remaining_cents', e.weekly_limit_cents - private.envelope_spent_cents(e.id)
        ) order by e.category)
        from public.envelopes e
        where e.user_id = p.id
      )
    )
    from public.profiles p
    where p.id = v_user_id
  );
end;
$$;

-- decision: 'approved' (auto), 'pending' (send the approval text), 'blocked' (another request is pending, nothing inserted)
create function public.request_spend(
  p_key text,
  p_amount_cents integer,
  p_category text,
  p_merchant text,
  p_description text default null
)
returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  v_user_id uuid := private.lock_user_by_key(p_key);
  v_pending_id uuid;
  v_envelope public.envelopes;
  v_allowance integer;
  v_over_envelope integer;
  v_over_total integer;
  v_over_by integer;
  v_status text;
  v_reason text;
  v_request_id uuid;
begin
  perform private.expire_pending(v_user_id);

  select id into v_pending_id
  from public.spend_requests
  where user_id = v_user_id and status = 'pending';

  if v_pending_id is not null then
    return jsonb_build_object(
      'decision', 'blocked',
      'reason', 'pending_exists',
      'pending_request_id', v_pending_id
    );
  end if;

  select * into v_envelope
  from public.envelopes
  where user_id = v_user_id and category = lower(p_category);

  if v_envelope.id is null then
    raise exception 'invalid_category' using errcode = '22023',
      hint = 'One of: food, travel, shopping, software';
  end if;

  select weekly_allowance_cents into v_allowance from public.profiles where id = v_user_id;

  v_over_envelope := greatest(0,
    p_amount_cents - (v_envelope.weekly_limit_cents - private.envelope_spent_cents(v_envelope.id)));
  v_over_total := greatest(0,
    p_amount_cents - (v_allowance - private.total_spent_cents(v_user_id)));
  v_over_by := greatest(v_over_envelope, v_over_total);

  if v_over_by > 0 then
    v_status := 'pending';
    v_reason := case when v_over_envelope >= v_over_total then 'over_envelope' else 'over_total' end;
  elsif p_amount_cents < v_envelope.auto_approve_under_cents then
    v_status := 'approved';
    v_reason := 'within_auto';
  else
    v_status := 'pending';
    v_reason := 'over_auto';
  end if;

  insert into public.spend_requests (
    user_id, envelope_id, amount_cents, merchant, description,
    status, reason, decided_by, decided_at, over_by_cents
  )
  values (
    v_user_id, v_envelope.id, p_amount_cents, p_merchant, p_description,
    v_status, v_reason,
    case when v_status = 'approved' then 'auto' end,
    case when v_status = 'approved' then now() end,
    v_over_by
  )
  returning id into v_request_id;

  return jsonb_build_object('decision', v_status) || private.request_json(v_request_id);
end;
$$;

create function public.get_status(p_key text, p_request_id uuid)
returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  v_user_id uuid := private.lock_user_by_key(p_key);
begin
  perform private.expire_pending(v_user_id);

  if not exists (select 1 from public.spend_requests where id = p_request_id and user_id = v_user_id) then
    raise exception 'request_not_found' using errcode = 'P0002';
  end if;
  return private.request_json(p_request_id);
end;
$$;

-- Applies a Y/N text reply to the sender's pending request.
-- result: 'approved' | 'declined' | 'expired' (replied too late) | 'no_pending' | 'invalid_reply' | 'unknown_phone'
create function public.resolve_reply(p_phone text, p_reply text)
returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  v_user_id uuid;
  v_reply text := upper(trim(coalesce(p_reply, '')));
  v_request public.spend_requests;
  v_status text;
begin
  select id into v_user_id
  from public.profiles
  where phone = '+' || ltrim(trim(p_phone), '+')
  for update;

  if v_user_id is null then
    return jsonb_build_object('result', 'unknown_phone');
  end if;

  perform private.expire_pending(v_user_id);

  select * into v_request
  from public.spend_requests
  where user_id = v_user_id and status = 'pending';

  if v_request.id is null then
    select * into v_request
    from public.spend_requests
    where user_id = v_user_id
    order by created_at desc
    limit 1;

    if v_request.status = 'expired' then
      return jsonb_build_object('result', 'expired') || private.request_json(v_request.id);
    end if;
    return jsonb_build_object('result', 'no_pending');
  end if;

  v_status := case
    when v_reply in ('Y', 'YES') then 'approved'
    when v_reply in ('N', 'NO') then 'declined'
  end;

  if v_status is null then
    return jsonb_build_object('result', 'invalid_reply') || private.request_json(v_request.id);
  end if;

  update public.spend_requests
  set status = v_status, decided_by = 'sms', decided_at = now()
  where id = v_request.id;

  return jsonb_build_object('result', v_status) || private.request_json(v_request.id);
end;
$$;

revoke all on function public.check_budget(text) from public, anon, authenticated;
revoke all on function public.request_spend(text, integer, text, text, text) from public, anon, authenticated;
revoke all on function public.get_status(text, uuid) from public, anon, authenticated;
revoke all on function public.resolve_reply(text, text) from public, anon, authenticated;
grant execute on function public.check_budget(text) to service_role;
grant execute on function public.request_spend(text, integer, text, text, text) to service_role;
grant execute on function public.get_status(text, uuid) to service_role;
grant execute on function public.resolve_reply(text, text) to service_role;
