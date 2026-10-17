-- Prerequisites that Supabase provides but a plain Postgres does not, so that
-- supabase/migrations/20261017214205_restore_rpc_schema.sql can be applied unchanged
-- to a local or scratch database. Used by scripts/init_db.py.
--
-- This creates NO application tables: the schema's single source of truth stays
-- supabase/migrations/. It only supplies the roles, the auth schema, pgcrypto in
-- the extensions schema, auth.uid(), and stub tables for the migration to drop.

-- Roles Supabase ships with.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role noinherit bypassrls;
  end if;
end
$$;

-- pgcrypto lives in the extensions schema on Supabase (digest, gen_random_bytes).
create schema if not exists extensions;
create extension if not exists pgcrypto with schema extensions;

-- A minimal stand-in for Supabase Auth. Only the columns the schema's trigger and
-- the demo seed touch; the real auth.users has many more.
create schema if not exists auth;

create table if not exists auth.users (
  instance_id uuid,
  id uuid primary key default gen_random_uuid(),
  aud text,
  role text,
  phone text unique,
  phone_confirmed_at timestamptz,
  raw_app_meta_data jsonb,
  raw_user_meta_data jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  confirmation_token text,
  recovery_token text,
  email_change_token_new text,
  email_change text,
  phone_change text,
  phone_change_token text,
  email_change_token_current text,
  reauthentication_token text
);

-- Supabase derives this from the request JWT. Locally there is no JWT, so it is null:
-- the "authenticated" RLS policies simply never match, which is correct for local use
-- (the MCP server connects as the owner/service role, not as an end user).
create or replace function auth.uid()
returns uuid
language sql
stable
as $$
  select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid;
$$;

grant usage on schema auth to anon, authenticated, service_role;
grant usage on schema extensions to anon, authenticated, service_role;

-- The migration begins by dropping the contract-shaped tables of the migration before
-- it. On a fresh database they do not exist, so create empty stubs for it to drop.
create table if not exists public.users (id uuid primary key default gen_random_uuid());
create table if not exists public.envelopes (id uuid primary key default gen_random_uuid());
create table if not exists public.requests (id uuid primary key default gen_random_uuid());
