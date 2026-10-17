-- Replace the first schema with the shared team contract (docs/SPEC.md + docs/TEAM-CONTRACT.md).
-- No Supabase Auth: the phone is verified through Twilio (Person 3). The approval rules live in the MCP server,
-- which talks to Postgres directly; the front writes server side with the secret key.
-- Money is numeric dollars with cents. The old tables were empty.

drop trigger on_auth_user_created on auth.users;
drop trigger on_auth_user_phone_updated on auth.users;
drop function public.check_budget(text);
drop function public.request_spend(text, integer, text, text, text);
drop function public.get_status(text, uuid);
drop function public.resolve_reply(text, text);
drop function public.rotate_connector_key();
drop table public.spend_requests;
drop table public.envelopes;
drop table public.profiles;
drop schema private cascade;

create table public.users (
  id uuid primary key default gen_random_uuid(),
  -- E.164, matches Twilio's From
  phone text unique not null check (phone ~ '^\+[1-9][0-9]{6,14}$'),
  -- the only auth of the connector link: random, URL-safe
  connector_key text unique not null check (connector_key ~ '^[A-Za-z0-9_-]+$'),
  weekly_budget numeric(10, 2) not null check (weekly_budget >= 0),
  created_at timestamptz not null default now()
);

create table public.envelopes (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users on delete cascade,
  -- free text chosen by the user
  category text not null check (category = trim(category) and length(category) between 1 and 50),
  weekly_limit numeric(10, 2) not null check (weekly_limit >= 0),
  auto_approve_under numeric(10, 2) not null check (auto_approve_under >= 0),
  created_at timestamptz not null default now()
);

create unique index one_envelope_per_category on public.envelopes (user_id, lower(category));

create table public.requests (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users on delete cascade,
  envelope_id uuid not null references public.envelopes on delete cascade,
  amount numeric(10, 2) not null check (amount > 0),
  merchant text,
  description text,
  url text,
  status text not null default 'pending' check (status in ('pending', 'approved', 'declined', 'expired')),
  created_at timestamptz not null default now(),
  decided_at timestamptz
);

-- One purchase in flight per user (the pending request is the lock)
create unique index one_pending_per_user on public.requests (user_id) where status = 'pending';
-- Budget math (spent / reserved per envelope)
create index requests_envelope_id_created_at_idx on public.requests (envelope_id, created_at);
-- Latest request per user, cascade deletes
create index requests_user_id_created_at_idx on public.requests (user_id, created_at desc);

-- Not reachable through the Data API with the publishable key: no policies on purpose.
-- MCP server and Twilio webhook connect as postgres (DATABASE_URL); the front uses the secret key server side.
alter table public.users enable row level security;
alter table public.envelopes enable row level security;
alter table public.requests enable row level security;

revoke all on public.users, public.envelopes, public.requests from anon, authenticated;
grant all on public.users, public.envelopes, public.requests to service_role;
