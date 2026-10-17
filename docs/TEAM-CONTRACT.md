# Wallie: MCP server integration notes (from Person 4)

What each team needs to build against so the pieces connect at integration. Times are PDT, 2026-10-17.

## Hosting (everyone)

- The Claude app reaches the MCP server from Anthropic's cloud, so the server must be on a public HTTPS URL. `localhost` only works for Claude Code.
- The MCP server runs behind an ngrok static domain for the demo, so the URL stays the same across restarts. Railway is the fallback. It is not tied to one machine: `mcp-server/README.md` sets it up from a clean clone on macOS, Linux or Windows, and `scripts/init_db.py` can create the schema and a demo user against any database, so anyone on the team can run it.
- Person 3's Twilio reply webhook also needs a public HTTPS URL. If you don't have one, tell Person 4.

## Person 1: setup and Connect Claude screen

- **Connector link:** `{MCP_BASE_URL}/mcp/{connector_key}`. Read `MCP_BASE_URL` from an env var. The real value arrives around 15:10.
- **connector_key:** random and URL-safe, at least 22 characters (`crypto.randomUUID()` or `secrets.token_urlsafe(16)`). Generate it when the user is created and store it in `users.connector_key`. It is the only auth, so it must not be guessable. The column enforces `^[A-Za-z0-9_-]+$`, so no other characters.
- **Claude Code command:** the spec's version is missing a flag. Without `--transport http`, Claude Code tries to run the link as a program. Use:
  `claude mcp add --transport http wallie {link}`
- **Claude app:** Settings → Connectors → Add custom connector → paste the link.
- **Categories are free text** chosen by the user; the four in the spec are examples. Don't allow two envelopes with the same name (case-insensitive) for one user — a unique index enforces it. Category names are trimmed, 1-50 characters.
- **Ignore my 14:20 note that said categories were the fixed four.** The schema went back and forth; as of 15:00 the merged schema on `main` is free text and the MCP server matches it. Sorry for the churn.
- **Phone numbers** go in E.164 format (`+15105551234`) so they match Twilio's `From`.

## Person 2: database

The merged schema on `main` (`supabase/migrations/20261017211932_align_with_team_contract.sql`) is what the MCP server is built against and tested on: `users` / `envelopes` / `requests`, numeric dollars, free-text categories, the `url` column, and both unique indexes. **Please don't swap it again** — `fix/restore-rpc-schema` would break the working server. If something in it must change, tell me first.

What I still need:

- **DATABASE_URL:** the Postgres connection string, sent privately (Supabase -> Connect -> Session pooler). It must be the `postgres` user: RLS is on with no policies, so the publishable key can't reach the tables.
- **Confirm the demo seed is applied** to the real project (`supabase/seed_demo.sql`) with the phone that receives the demo texts, and tell me the connector key if it isn't `demo`.

Budget definitions the server uses (please match them in the UI so the numbers agree):

- spent = sum of `approved` requests with `created_at` in the last 7 days
- reserved = sum of `pending` requests
- remaining = `weekly_limit` - spent - reserved

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
