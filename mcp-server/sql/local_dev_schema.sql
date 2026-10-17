-- Convenience mirror of the CURRENT schema for running against a plain Postgres
-- (local Docker, a scratch database) where Supabase's auth schema does not exist.
--
-- SOURCE OF TRUTH is supabase/migrations/. Do not edit this to change the schema;
-- change it there, then mirror it here. The migrations cannot be replayed directly on
-- plain Postgres because the first one references auth.users and the service_role role.
--
-- Matches: supabase/migrations/20261017211932_align_with_team_contract.sql

create table if not exists public.users (
  id uuid primary key default gen_random_uuid(),
  -- E.164, matches Twilio's From
  phone text unique not null check (phone ~ '^\+[1-9][0-9]{6,14}$'),
  -- the only auth of the connector link: random, URL-safe
  connector_key text unique not null check (connector_key ~ '^[A-Za-z0-9_-]+$'),
  weekly_budget numeric(10, 2) not null check (weekly_budget >= 0),
  created_at timestamptz not null default now()
);

create table if not exists public.envelopes (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users on delete cascade,
  -- free text chosen by the user
  category text not null check (category = trim(category) and length(category) between 1 and 50),
  weekly_limit numeric(10, 2) not null check (weekly_limit >= 0),
  auto_approve_under numeric(10, 2) not null check (auto_approve_under >= 0),
  created_at timestamptz not null default now()
);

create unique index if not exists one_envelope_per_category
  on public.envelopes (user_id, lower(category));

create table if not exists public.requests (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users on delete cascade,
  envelope_id uuid not null references public.envelopes on delete cascade,
  amount numeric(10, 2) not null check (amount > 0),
  merchant text,
  description text,
  url text,
  status text not null default 'pending'
    check (status in ('pending', 'approved', 'declined', 'expired')),
  created_at timestamptz not null default now(),
  decided_at timestamptz
);

-- One purchase in flight per user (the pending request is the lock)
create unique index if not exists one_pending_per_user
  on public.requests (user_id) where status = 'pending';
create index if not exists requests_envelope_id_created_at_idx
  on public.requests (envelope_id, created_at);
create index if not exists requests_user_id_created_at_idx
  on public.requests (user_id, created_at desc);
