# Database

Supabase project `ucsysxhiydfgukkkbtdx`. Implements the shared contract of `docs/SPEC.md` + `docs/TEAM-CONTRACT.md`. Schema source of truth: `migrations/`. TypeScript types: `types.ts`.

## Tables

- `users`: `phone` (E.164, unique, matches Twilio's `From`), `connector_key` (unique, URL-safe), `weekly_budget`
- `envelopes`: `user_id`, `category` (free text, trimmed, unique per user case-insensitively), `weekly_limit`, `auto_approve_under`
- `requests`: `user_id`, `envelope_id`, `amount` (> 0), `merchant`, `description`, `url`, `status` (`pending | approved | declined | expired`), `created_at`, `decided_at`

Money is `numeric(10,2)` dollars (test dollars). Deleting a user deletes their envelopes and requests.

## Guarantees

- One pending request per user: unique index `one_pending_per_user`. A second insert fails with `unique_violation` (23505).
- Budget definitions (same everywhere): spent = `approved` in the last 7 days, reserved = `pending`, remaining = `weekly_limit` − spent − reserved.
- Expiry is lazy: a `pending` older than 10 minutes is set to `expired` (with `decided_at`) by whoever touches it next. Resolve with `where id = ... and status = 'pending'`.

## Access

RLS is enabled with **no policies**, and `anon`/`authenticated` have no grants: the publishable key can't reach any table.

- **MCP server (Person 4), Twilio webhook (Person 3)**: Postgres directly with `DATABASE_URL` (Supabase → Connect → Session pooler). Shared privately, never committed.
- **Front (Person 1)**: server side only (route handler / server action) with the secret key, never in a `NEXT_PUBLIC_` variable.

## Demo user

`seed_demo.sql`: connector key `demo`, Software envelope $75/week, auto-approve under $10. Put the demo phone in it, then run it (idempotent).

## Workflow (no Docker)

```
npx supabase migration new <name>          # write SQL
npx supabase db push                       # apply to the project
npx supabase gen types --lang typescript --linked > supabase/types.ts
npx supabase db query --linked -f supabase/tests/scenarios.sql   # rolled back, must print "all scenarios passed"
npx supabase db advisors --linked
```

Never change the schema from the dashboard.
