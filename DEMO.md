# Running the demo

The 30-second story: **set an allowance on the website → paste the link into Claude →
Claude asks to spend $29 → you approve → Claude confirms and shares the purchase link.**

The demo has two halves, and it helps to know which is which:

| Half | Where it happens |
| --- | --- |
| **Setup** — phone, allowance, envelopes, get your connector link | the website |
| **Purchase** — Claude asks, you approve, Claude confirms | inside Claude |

The website has no "buy" button on purpose. The whole point is that *Claude* asks to
spend on your behalf, so the purchase has to start in Claude. The website's job ends at
handing you a link. (The text thread on the landing page is a scripted illustration — it
looks like the real thing but talks to nothing.)

---

## Part 0 — one-time setup on your own machine

Do this once and the demo no longer depends on anyone else's terminal being open.
You need the **MCP server** and a **tunnel** running somewhere; if that somewhere is your
laptop, you own the whole demo.

**1. Get the code and dependencies**

```sh
git clone https://github.com/doodsito/side-project-c0mpiled-14.git
cd side-project-c0mpiled-14/mcp-server
uv sync
```

No `uv`? macOS: `brew install uv`. Windows PowerShell:
`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`.

**2. Create `mcp-server/.env`** — one line is enough:

```
DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-us-east-1.pooler.supabase.com:5432/postgres
```

That is the Supabase **session pooler** URI (dashboard → Connect → Session pooler), as
the `postgres` user. `.env` is gitignored and must stay that way. Leave `NOTIFY_URL`
unset for now: approval messages then print to this terminal instead of being texted.

**3. Install ngrok and claim the tunnel**

```sh
ngrok config add-authtoken <your authtoken>
```

> **Only one ngrok session can hold the domain at a time.** If someone else is already
> serving `sepia-pupil-giddily.ngrok-free.dev`, yours will be refused until they stop —
> coordinate before the demo.

**4. Check it works**

```sh
uv run pytest                       # 13 tests; the SQL-parity ones need DATABASE_URL
```

---

## Part 1 — start it (two terminals, every time)

```sh
# Terminal A — the MCP server. KEEP THIS VISIBLE: the approval message prints here.
cd mcp-server
./scripts/dev.sh                    # Windows: uv run uvicorn server:app --port 8000

# Terminal B — the public tunnel, so the Claude app can reach you
ngrok http --url=sepia-pupil-giddily.ngrok-free.dev 8000
```

Confirm both in one call:

```sh
curl https://sepia-pupil-giddily.ngrok-free.dev/health
# {"ok":true,"service":"wallie-mcp","database":"ready"}
```

`"ok":true` means the server *and* its database answered. Anything else — fix it before
demoing, because every connector link depends on it.

**Reset to a clean slate before each run**, so the $75 is untouched and no stale request
holds the lock:

```sh
cd mcp-server
uv run python scripts/reset_demo.py --all
```

---

## Part 2 — the demo

### 1. Setup, on the website (~20s)

```sh
npx serve frontend
```

Landing page → **Set up Wallie** → verify phone (a real code arrives by SMS) → weekly
allowance → envelopes → **Connect Claude**.

Use the phone that is **verified in Twilio**: on a trial account Supabase can only text
verified numbers, so an unverified phone never receives the code.

The last screen shows your connector link, the Claude Code command, and a status line
confirming the server is reachable. **Copy the link.**

For the software envelope use the demo numbers: **limit $75, auto-approve under $10.**

### 2. Give the link to Claude

- **Claude app:** Settings → Connectors → Add custom connector → paste the link.
- **Claude Code:** `claude mcp add --transport http wallie <the link>`
  (`--transport http` is required, or it tries to run the URL as a program.)

Then paste the line from the last setup screen into Claude's instructions:

> Before any purchase, check with Wallie.

### 3. Ask Claude to buy something

> **Buy me a $29 Notion subscription.**

Claude calls `request_spend` and says it's waiting for your approval — $29 is over the
$10 auto-approve line.

### 4. Approve it

The message is in **Terminal A**:

```
[notify:console] to +1650*****50: Wallie: Claude wants to spend $29.00 at Notion (software). $75.00 left this week. Reply Y or N.
```

Answer as the user:

```sh
cd mcp-server
uv run python scripts/reply.py y        # or n, to show a decline
```

`reply.py` finds the waiting request on its own — no key or phone needed.

### 5. Claude confirms

Claude is polling `get_status`, sees `approved`, confirms, and **shares the purchase link
rather than buying anything itself.** Say that last part out loud — it is the product.

---

## Worth showing if you have 20 more seconds

| Say to Claude | What happens | Why it matters |
| --- | --- | --- |
| "Buy a $5 Raycast subscription" | approved instantly | under the $10 auto-approve line |
| "Buy a $90 Delta flight" | asks approval, *over by $15* | over the envelope's $75 |
| Ask for a second purchase while one waits | `busy`, and Claude explains instead of retrying | one purchase in flight per user, so a bare "Y" is never ambiguous |
| "What's my budget?" | per-category limit, spent, remaining | `check_budget` |

The `busy` case is the most interesting technically: a pending request *is* the lock, and
its id is the key the requesting agent holds.

---

## When it goes wrong

| Symptom | Cause | Fix |
| --- | --- | --- |
| Claude: connector not found / link invalid | server or tunnel down | `curl .../health`; restart both terminals |
| ngrok refuses to start | someone else holds the domain | they must stop their tunnel first |
| No SMS code during setup | phone not verified on the Twilio trial | verify it, or skip to Part 2 step 2 with an existing link |
| `error`: not connected to its database | `DATABASE_URL` missing or wrong | check `mcp-server/.env`; `curl .../health` |
| `busy` when you expect `pending` | a request from an earlier run is still pending | `uv run python scripts/reset_demo.py --all` |
| `expired` before you replied | more than 10 minutes passed | reset and redo; the expiry is deliberate |
| Remaining isn't $75 | approved purchases count for 7 days | `uv run python scripts/reset_demo.py --all` |
| `error`: could not send the approval text | the texting service failed | Terminal A logs the status and a hint; or blank `NOTIFY_URL` for console mode |

Nothing spends real money — every amount is test dollars.

---

## Switching to real texts

While `NOTIFY_URL` is blank, approvals print to Terminal A. Once the texting service is
up:

1. In `mcp-server/.env`, set `NOTIFY_URL` to its `/notify` URL and `NOTIFY_SECRET` to the
   shared secret.
2. Restart Terminal A.
3. `curl https://sepia-pupil-giddily.ngrok-free.dev/health`

Then step 4 above becomes: the text arrives on your phone and you reply **Y** by SMS —
no `reply.py`. That also needs the Twilio inbound webhook saved (Phone Numbers → the
number → Messaging → "A message comes in" → Webhook, POST, the service's reply URL),
otherwise the text goes out but your reply never comes back.
