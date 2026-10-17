# Wallie: MCP server integration notes (from Person 4)

What each team needs to build against so the pieces connect at integration. Times are PDT, 2026-10-17.

## Hosting (everyone)

- The Claude app reaches the MCP server from Anthropic's cloud, so the server must be on a public HTTPS URL. `localhost` only works for Claude Code.
- The MCP server runs on Person 4's laptop behind an ngrok static domain, so the URL stays the same across restarts. Railway is the fallback.
- Person 3's Twilio reply webhook also needs a public HTTPS URL. If you don't have one, tell Person 4.

## Person 1: setup and Connect Claude screen

- **Connector link:** `{MCP_BASE_URL}/mcp/{connector_key}`. Read `MCP_BASE_URL` from an env var. The real value arrives around 15:10.
- **connector_key:** generated server-side by the DB's `rotate_connector_key()` RPC (`wk_` + 48 hex chars), shown once, stored only as a hash. Call it right after the user is created and show the result as their connector link; it is the only auth, so it must not be guessable.
- **Claude Code command:** the spec's version is missing a flag. Without `--transport http`, Claude Code tries to run the link as a program. Use:
  `claude mcp add --transport http wallie {link}`
- **Claude app:** Settings → Connectors → Add custom connector → paste the link.
- **Correction (14:20): categories are the fixed four, not free text.** Person 2's schema constrains `envelopes.category` to exactly `food, travel, shopping, software` and creates all four automatically when a user signs up (an `auth.users` insert trigger). If your setup screen has free-text category input, change it to editing the limits on those 4 existing rows — don't create or name new envelopes.
- **Phone numbers** go in E.164 format (`+15105551234`) so they match Twilio's `From`.

## Person 2: database (superseded 14:20 — schema already merged, see below)

The section below is what the original plan asked for. Person 2 has already shipped something more complete (`supabase/migrations/`, `supabase/README.md`): `profiles`/`envelopes`/`spend_requests` tables plus `check_budget`/`request_spend`/`get_status`/`resolve_reply` RPCs that do the budget math and the one-pending-per-user lock in SQL. The MCP server calls those RPCs directly instead of re-deriving this logic. Two asks that are still open:

- **DATABASE_URL:** send Person 4 the Postgres connection string privately (Supabase → Connect → Session pooler, project `ucsysxhiydfgukkkbtdx`).
- **Demo user:** since users (and their 4 envelopes) are only created via an `auth.users` insert trigger, please create one for the demo (phone = whoever receives the demo texts), set its Software envelope to `weekly_limit_cents 7500` / `auto_approve_under_cents 1000`, and run `rotate_connector_key()` for it — send Person 4 the resulting key.
- No longer needed: the `url text` column and the two unique indexes below — `spend_requests` already has an equivalent unique index, and envelope names are the fixed four so no per-category index is needed.

<details><summary>Original ask (superseded)</summary>

```sql
alter table requests add column url text;
create unique index one_pending_per_user on requests (user_id) where status = 'pending';
create unique index one_envelope_per_category on envelopes (user_id, lower(category));
```

</details>

## Person 3: texting

- **Outgoing text.** When a purchase needs approval, the MCP server saves it as `pending`, then calls you:
  - Request: `POST {NOTIFY_URL}` with header `x-wallie-secret: {NOTIFY_SECRET}` and JSON:

    ```json
    {"request_id": "uuid", "phone": "+15105551234", "kind": "approval",
     "amount": 29.00, "category": "Software", "merchant": "Notion",
     "description": "Notion Plus, 1 month", "url": "https://...",
     "remaining": 75.00, "over_by": null}
    ```

  - `kind` is `approval` or `over_budget`. `over_by` is set only for `over_budget`.
  - Return 2xx within 5 seconds, once Twilio has accepted the SMS. On a non-2xx or a timeout, the MCP server marks the request `expired` and tells Claude to try again.
  - Suggested wording:
    - `approval`: "Wallie: Claude wants to spend $29.00 at Notion (Software). $75.00 left this week. Reply Y or N."
    - `over_budget`: "Wallie: Claude wants $90.00 at Delta (Travel). Over by $15.00. Approve anyway? Y/N"
- **Y/N reply webhook:**
  - Find the user by phone, then their one `pending` request.
  - Older than 10 minutes: set it to `expired` and reply "That request expired."
  - Otherwise update with `where id = ... and status = 'pending'`: Y sets `approved`, N sets `declined`, and both set `decided_at = now()`. The `status = 'pending'` condition stops a reply and an expiry from colliding.
  - Nothing pending: reply "Nothing is waiting for approval."
- **What Person 4 needs:** `NOTIFY_URL` and `NOTIFY_SECRET` by around 15:15.
- **Twilio trial accounts** can only text verified numbers, so verify the demo phone.

## Concurrency: one purchase at a time per user

- A pending request acts as a lock. While a user has one waiting for Y/N, every other `request_spend` for that user is refused as `busy`. That holds even if it comes from another Claude window or another agent. The lock frees on Y, N or the 10-minute expiry.
- **The key:** the `request_id` is the key the agent holds. Only that agent polls it and shares the link once approved.
- **Enforcement:** the unique index above, plus a row lock while the server checks the budget. Two requests arriving at the same moment can't both pass the check.
