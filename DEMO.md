# Running the demo

The 30-second story: **Software envelope $75/week, auto-approve under $10 → Claude asks
to spend $29 → the user is asked to approve → they say Y → Claude confirms and shares
the purchase link.**

Status: working end to end. The approval message currently prints to the **MCP server's
terminal** instead of arriving as a real SMS, because the texting service is being fixed
(see *If you want real texts* at the bottom). Everything else is live.

## Before you start

Two processes must be running on the MCP server machine. Both are already up; this is
how to bring them back if something dies.

```sh
# 1. the MCP server (keep this terminal visible during the demo - the approval
#    message prints here)
cd mcp-server
./scripts/dev.sh

# 2. the public tunnel, so the Claude app can reach it
ngrok http --url=sepia-pupil-giddily.ngrok-free.dev 8000
```

Check both at once:

```sh
curl https://sepia-pupil-giddily.ngrok-free.dev/health
# {"ok":true,"service":"wallie-mcp","database":"ready"}
```

`"ok":true` means the server **and** its database are answering. Anything else, fix that
before demoing.

**Reset to a clean slate** (do this right before each run, so the $75 is untouched and
no stale request is holding the lock):

```sh
cd mcp-server
uv run python scripts/reset_demo.py
```

## The demo

### 1. Show the product (optional, ~20s)

```sh
npx serve frontend
```

Landing page → **Set up Wallie** → the four setup screens. Phone sign-in is live, so a
real phone gets a real code. The last screen shows the connector link and the Claude Code
command, and a status line confirming the server is reachable.

> Skip this if time is short, or if you'd rather not depend on an SMS code mid-demo. The
> purchase flow below does not need it — it uses the already-seeded demo user.

### 2. Ask Claude to buy something

In a Claude Code session (or the Claude app, with the connector added):

> **Buy me a $29 Notion subscription.**

Claude calls `request_spend` and reports it's waiting for approval. Say out loud that
Wallie has asked the user to confirm, because $29 is over the $10 auto-approve line.

### 3. Approve it

The approval message is in the MCP server's terminal:

```
[notify:console] to +1650*****50: Wallie: Claude wants to spend $29.00 at Notion (software). $75.00 left this week. Reply Y or N.
```

Answer as the user:

```sh
cd mcp-server
uv run python scripts/reply.py y      # or n, to show a decline
```

### 4. Claude confirms

Claude is polling `get_status`, sees `approved`, confirms, and **shares the purchase
link rather than buying anything itself.** That last part is the point of the product —
say it out loud.

## Worth showing if you have time

| Say this to Claude | What happens | Why it matters |
| --- | --- | --- |
| "Buy a $5 Raycast subscription" | approved instantly, no approval needed | under the $10 auto-approve line |
| "Buy a $90 flight on Delta" | asks for approval, says *over by $15* | over the envelope's $75 |
| Ask for a second purchase while one is waiting | `busy` — Claude declines to proceed and explains | one purchase in flight per user; a bare "Y" is never ambiguous |
| "What's my budget?" | per-category limit, spent, remaining | `check_budget` |

The `busy` case is the most interesting one technically: a pending request *is* a lock,
and its id is the key the requesting agent holds.

## If it goes wrong

| Symptom | Cause | Fix |
| --- | --- | --- |
| Claude says the link is invalid / connector not found | server or tunnel down | `curl .../health`; restart both commands above |
| `error`: "not connected to its database" | `DATABASE_URL` missing or Supabase unreachable | check `mcp-server/.env` |
| `busy` when you expect `pending` | a request from an earlier run is still pending | `uv run python scripts/reset_demo.py` |
| `expired` before you replied | more than 10 minutes passed | reset and redo; expiry is deliberate |
| Remaining is not $75 | earlier approved purchases count for 7 days | `uv run python scripts/reset_demo.py` |
| `error`: "could not send the approval text" | the texting service is failing | the MCP server's log says why; or blank `NOTIFY_URL` in `.env` for console mode |

Nothing here spends real money — all amounts are test dollars.

## If you want real texts

The texting service's `/notify` was returning 502 at 16:45, so `mcp-server/.env` has its
URL commented out and the server prints the message instead. Once it's fixed:

1. In `mcp-server/.env`, uncomment the `NOTIFY_URL=...` line and delete the blank
   `NOTIFY_URL=` below it.
2. Restart: `./scripts/dev.sh`
3. Confirm: `curl https://sepia-pupil-giddily.ngrok-free.dev/health`
4. Run step 2 above — the message arrives as an SMS, and the user replies **Y** by text
   instead of using `scripts/reply.py`.

That also needs the Twilio inbound webhook saved (Phone Numbers → the number →
Messaging → "A message comes in" → Webhook, POST, the service's `/sms` URL), otherwise
the text goes out but the reply never comes back.
