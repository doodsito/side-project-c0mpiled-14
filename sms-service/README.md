# Wallie SMS service

Sends the approval text, and applies the user's Y/N reply.

**Written by the MCP server person as a starting point — it's yours. Change or replace
any of it.** It is already tested against the real Supabase project: a $29 request
reached `/notify`, a simulated Twilio `Y` reached `/sms-reply`, and Claude saw
`approved`. The only untested part is Twilio actually sending (no credentials here).

## The two values the MCP server needs

This is the part that confuses people, so plainly:

**You are not looking these up anywhere. You create them.**

| Value | What it is | Where it comes from |
| --- | --- | --- |
| `NOTIFY_SECRET` | A password shared between this service and the MCP server, so this service knows a request is genuine | **You invent it.** Any long random string. Run `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` and use the output |
| `NOTIFY_URL` | The public address of *this service's* `/notify` endpoint | **It doesn't exist until you deploy this.** Once it's running at a public HTTPS address, `NOTIFY_URL` is that address + `/notify` |

Neither is in the Twilio console. Neither is in the database. Send both to the MCP
server person once you have them.

What you *do* get from the Twilio console (for this service, not for them):
`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and your Twilio phone number.

## Setup

```sh
cd sms-service
uv sync
cp .env.example .env     # then fill it in
uv run uvicorn main:app --port 9000
```

`.env` needs:

- `NOTIFY_SECRET` — the string you generated above
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` — https://console.twilio.com, on the front page under Account Info
- `TWILIO_FROM` — your Twilio number in E.164 (`+15105551234`)
- `DATABASE_URL` — the same Postgres URI the MCP server uses (ask for it; Supabase → Connect → Session pooler)

**Leave the Twilio values blank to start.** Without them the service prints the text
instead of sending it, so you can test everything before touching Twilio:

```
[dry-run] would text +16506805850: Wallie: Claude wants to spend $29.00 at Notion (software). $75.00 left this week. Reply Y or N.
```

Check it's up: `curl http://localhost:9000/` returns what is and isn't configured.

## Going public

Both endpoints need to be reachable from the internet — the MCP server calls `/notify`,
and Twilio calls `/sms-reply`. Any tunnel works; this needs no account:

```sh
cloudflared tunnel --url http://localhost:9000
```

It prints a `https://....trycloudflare.com` URL. So:

- **`NOTIFY_URL`** = `https://....trycloudflare.com/notify` → send this to the MCP server person
- In the **Twilio console** → Phone Numbers → your number → *A message comes in* →
  Webhook, **POST**, `https://....trycloudflare.com/sms-reply`

The quick-tunnel URL changes every restart. If replies stop arriving, that's the first
thing to check — re-paste the new URL in both places.

## Twilio trial accounts

A trial can only text **verified** numbers. Verify the demo phone first, or the send
fails: Twilio console → Phone Numbers → Verified Caller IDs.

## What it does

```
MCP server --POST /notify--> this service --> Twilio --> user's phone
                                                            |
                                                         "Y" or "N"
                                                            v
user's phone --> Twilio --POST /sms-reply--> this service --> resolve_reply() in Postgres
```

`/notify` checks `x-wallie-secret`, formats the message, sends it, and returns 2xx. If it
returns non-2xx the MCP server cancels the request and tells Claude to retry, so don't
return 200 on a failed send.

`/sms-reply` hands the reply straight to `resolve_reply(phone, body)` in the database and
answers with TwiML. **That one function does all the work** — finds the user by phone,
applies the 10-minute expiry, updates the pending request, and returns
`approved | declined | expired | no_pending | invalid_reply | unknown_phone`. Don't
re-implement expiry or the status update; a `where status = 'pending'` guard in there is
what stops a late Y from colliding with an expiry.

## Message wording

In `approval_text()` in `main.py`. Currently matches the team contract:

- `Wallie: Claude wants to spend $29.00 at Notion (software). $70.00 left this week. Reply Y or N.`
- `Wallie: Claude wants $90.00 at Delta (software). Over by $15.00. Approve anyway? Y/N`

Reply wording is in `REPLIES`. Patrick owns copy, so run changes past them.

## Verified behaviour

| Input | Response |
| --- | --- |
| `/notify` with the right secret | sends, returns `{"ok": true}` |
| `/notify` with a wrong secret | `401` |
| `Y` with one pending | approves, "Approved. Wallie will share the purchase link with Claude." |
| `N` with one pending | declines, "Declined. Wallie told Claude not to buy it." |
| `maybe` with one pending | "Please reply Y to approve or N to decline." |
| `Y` with nothing pending | "Nothing is waiting for approval." |
| `Y` from an unknown number | "This number is not set up with Wallie." |
