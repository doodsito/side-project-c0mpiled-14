# Wallie: MCP server integration notes (from the MCP server person)

What each team needs to build against so the pieces connect at integration. Times are PDT, 2026-10-17.

## Money units (everyone) — read this one

One rule, no exceptions: **if a field name ends in `_cents` it is an integer number of cents; if it does not, it is dollars.**

| Where | Field | Unit | Example |
| --- | --- | --- | --- |
| Database and RPCs | `amount_cents`, `remaining_cents`, `weekly_limit_cents`, `auto_approve_under_cents`, `weekly_allowance_cents` | integer cents | `7500` |
| MCP tool results (what Claude sees) | `amount`, `remaining`, `weekly_limit`, `auto_approve_under`, `over_by` | dollars | `75.0` |
| `POST {NOTIFY_URL}` payload | `amount`, `remaining`, `over_by` | **dollars** | `29.0` |
| What the website writes | the `*_cents` columns | integer cents | `7500` |

So: the SMS person formats the notify payload directly (`f"${remaining:.2f}"`) and must **not** divide by 100. The website multiplies by 100 before writing. The MCP server converts at its own boundary, so Claude only ever sees dollars.

## Hosting (everyone)

- The Claude app reaches the MCP server from Anthropic's cloud, so the server must be on a public HTTPS URL. `localhost` only works for Claude Code.
- The MCP server runs behind an ngrok **static domain**: `https://sepia-pupil-giddily.ngrok-free.dev`. It survives restarts of the tunnel and the server, so connector links stay valid (verified). Health check: `curl https://sepia-pupil-giddily.ngrok-free.dev/health`. Railway is the fallback. It is not tied to one machine: `mcp-server/README.md` sets it up from a clean clone on macOS, Linux or Windows, and `scripts/init_db.py` can create the schema and a demo user against any database, so anyone on the team can run it.
- The SMS person's Twilio reply webhook also needs a public HTTPS URL. If you don't have one, let me know.

## The website person: setup and Connect Claude screen

- **Connector link:** `{MCP_BASE_URL}/mcp/{connector_key}`. **`MCP_BASE_URL` is now set** in `frontend/config.js` to `https://sepia-pupil-giddily.ngrok-free.dev` — nothing more needed from you on this.
- **connector_key:** don't generate it yourself. Call the DB's `rotate_connector_key()` RPC as the signed-in user; it returns the key once (`wk_` + 48 hex) and stores only a sha256 hash. Show it on the Connect screen and warn that it won't be shown again. It is the only auth, so it must not be guessable or shared.
- **Claude Code command:** the spec's version is missing a flag. Without `--transport http`, Claude Code tries to run the link as a program. Use:
  `claude mcp add --transport http wallie {link}`
- **Claude app:** Settings → Connectors → Add custom connector → paste the link.
- **Categories are the FIXED FOUR: `food`, `travel`, `shopping`, `software`.** This is final as of 15:20 — the schema constrains them and creates all four automatically when a user signs up, so your envelopes screen should **edit the limits on those four existing rows**, never create or name new ones. (This flip-flopped between 14:14 and 15:20 and I sent you the wrong thing twice — sorry. The current repo is the truth; if you built a free-text input, that's the one thing that needs changing.)
- **Setup writes:** `profiles.weekly_allowance_cents`, and `envelopes.weekly_limit_cents` / `auto_approve_under_cents`. All money is **integer cents** (`2900` = $29.00).
- **Phone verification is Supabase Phone OTP** (`signInWithOtp` then `verifyOtp`), with Twilio as the SMS provider — profiles and envelopes are created by a trigger on signup, so don't insert them yourself.
- **Phone numbers** go in E.164 format (`+15105551234`) so they match Twilio's `From`.

## The database person

**The RPC schema (`20261017214205_restore_rpc_schema.sql`) is what the MCP server now runs on, and it is verified working end to end.** The schema changed three times between 14:14 and 15:20 and each flip broke one side of the integration, so: **this is frozen. No more schema swaps before the demo.** If something genuinely must change, message me first and we change it together.

For the record on how the loop happened: your 14:47 commit cited `mcp-server/db.py` as the reason to revert, but I had just changed `db.py` to match your 14:34 schema — we were each following the other. Guard added on my side: `tests/test_sql_parity.py` asserts your `request_spend` RPC agrees with my reference `rules.py`, so if the decision logic ever diverges a test fails instead of the demo.

What I still need:

- **DATABASE_URL** — the only thing blocking me from testing against the real project. Supabase -> Connect -> Session pooler, sent privately. Must be the `postgres`/`service_role` user; the RPCs are granted to `service_role` only.
- **Confirm `supabase/seed_demo.sql` is applied** with the phone that receives the demo texts, and send me the connector key you set (the seed deliberately doesn't commit one).
- **Nice to have if there's time:** `alter table public.spend_requests add column url text;` so an approved purchase link survives into `get_status`. Not demo-blocking.

## The SMS person: texting

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
- **What I need from you:** `NOTIFY_URL` and `NOTIFY_SECRET` by around 15:15.
- **Twilio trial accounts** can only text verified numbers, so verify the demo phone.

## Concurrency: one purchase at a time per user

- A pending request acts as a lock. While a user has one waiting for Y/N, every other `request_spend` for that user is refused as `busy`. That holds even if it comes from another Claude window or another agent. The lock frees on Y, N or the 10-minute expiry.
- **The key:** the `request_id` is the key the agent holds. Only that agent polls it and shares the link once approved.
- **Enforcement:** the unique index above, plus a row lock while the server checks the budget. Two requests arriving at the same moment can't both pass the check.
