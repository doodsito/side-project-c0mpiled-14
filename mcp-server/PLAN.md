# MCP server handoff plan (Person 4)

You are the agent building Wallie's MCP server. This file is the source of truth for our part. Read it top to bottom before doing anything, then skim `docs/SPEC.md` (product) and `docs/TEAM-CONTRACT.md` (what other teams build against). Then `git log --oneline -15` and continue from the first unchecked item in **Build steps**.

After each step: tick it, add a line to **Log**, commit to `main` (see **Working rules**). If your context gets refreshed, the next instance starts here again.

## Situation

- **Product:** Wallie gives Claude a weekly spending allowance in test dollars, split into user-defined categories ("envelopes"). Claude must ask Wallie before buying. Small amounts are auto-approved; bigger ones text the user, who replies Y or N. After approval Claude shares the purchase link and the user buys it themselves.
- **Us:** Person 4. We build the MCP server: tools `check_budget`, `request_spend`, `get_status`, the approval rules, and the concurrency lock. It must work in the Claude app and Claude Code.
- **Other teams:**
  - Person 1: landing page + setup screens, writes users/envelopes to Supabase.
  - Person 2: Supabase schema.
  - Person 3: Twilio texts + Y/N reply webhook.
  - Patrick: copy, testing, demo.
- **Clock:** started 13:42 PDT 2026-10-17. Team integration at **~15:45 PDT**. Demo path beats everything:
  > Software envelope $75/week, auto-approve under $10 → Claude requests a $29 subscription → text arrives → reply Y → Claude confirms and shares the link.
- **User = Person 4's human.** They relay messages to the other teams. When you need something from another team, tell the user exactly what to ask for (see **Dependency queue**). Don't wait silently.

## Locked decisions

| # | Decision |
| --- | --- |
| D1 | **Python 3.12**, managed with `uv`. Packages: `fastmcp` (MCP server, Streamable HTTP), `psycopg[binary,pool]` (Postgres, async), `httpx` (notify call), `python-dotenv`, `uvicorn`; `pytest` for dev. Node is NOT installed on this machine. |
| D2 | **Connector link:** `https://<MCP_BASE_URL>/mcp/<connector_key>`. No OAuth. The key in the path identifies the user. Claude Code: `claude mcp add --transport http wallie <link>`. |
| D3 | **Hosting:** the Claude app needs public HTTPS. Run on this laptop behind an **ngrok static domain** (stable URL). Fallback: Railway. Claude Code can use `http://localhost:8000/...` for local testing. |
| D4 | **Texts:** we insert the `pending` row, then `POST NOTIFY_URL` to Person 3 (payload in `docs/TEAM-CONTRACT.md`). Non-2xx or >5 s timeout → mark the request `expired`, tell Claude to retry. If `NOTIFY_URL` is unset (dev), print the text to the console and use `scripts/reply.py` to simulate Y/N. |
| D5 | **Expiry is lazy, with no cron.** A `pending` row older than 10 min is set to `expired` by whoever touches it next: us in `request_spend`/`get_status`, Person 3 in the reply webhook. |
| D6 | **SUPERSEDED 14:20.** Person 2 built the budget math *and* the decision/lock as Postgres RPCs (`public.check_budget`, `public.request_spend`, `public.get_status`), atomic inside each function. `db.py` calls these RPCs rather than running its own transactional SQL. spent = approved in the last 7 days; reserved only matters as "has a pending row" (reserved amounts aren't subtracted from `remaining_cents` server-side — see `supabase/README.md`). |
| D7 | **SUPERSEDED 14:20.** Categories are the **fixed four** (`food`, `travel`, `shopping`, `software`), enforced by a check constraint in Person 2's schema and auto-created per user. Match `category` case-insensitively against those four; the `request_spend` RPC raises `invalid_category` (hint lists the four) on no match. No "zero envelopes" case — every user gets all four at signup. |
| D8 | **Concurrency ("agent key"):** one purchase in flight per user. A `pending` request is a lock (lease). Its `request_id` is the key the requesting agent holds. While it exists, any other `request_spend` for that user returns `busy`, even for amounts that would be auto-approved. **Exception:** a retry with the same amount + merchant (case-insensitive) returns the existing pending request. The lock is released by Y, N or expiry. See **Concurrency design**. |
| D9 | **Waiting for Y/N:** `get_status` long-polls the DB every 1 s for up to 25 s and returns early on any change. Claude calls it again while it is still `pending`. `request_id` is optional and defaults to the user's latest request. |
| D10 | **SUPERSEDED 14:20 (partial).** `spend_requests` has no `url` column, so a purchase link isn't persisted. `request_spend` still takes an optional `url`; our server passes it to the notify text but `get_status` can't return it on a later poll. Fine for the demo (the same Claude turn that called `request_spend` already has the url in context); revisit with Person 2 post-demo if time allows. |
| D11 | **Out of scope:** wally/RunAnywhere (not used), OAuth, the `users.weekly_budget` total (shown in `check_budget`, not enforced), a cancel tool (stretch only). |
| D12 | **(new, 14:20) `db.py` is an RPC adapter, not a raw-SQL layer.** It calls `select check_budget($1)`, `select request_spend($1,$2,$3,$4,$5)`, `select get_status($1,$2)` over the same psycopg pool, converts cents↔dollars at the boundary, maps `decision: 'blocked'` → our `busy` (adding the D8 same-amount-same-merchant idempotent-retry check, which the RPC doesn't do), and maps raised exceptions (`invalid_connector_key`, `invalid_category`, `request_not_found`) to our `error` status. See `supabase/README.md` for the real RPC contract — it supersedes this file's **Architecture** and **Tools** sketches wherever they disagree (money units, field names). |

## Approval rules (`rules.py`, pure function, unit-tested)

```python
def decide(amount, weekly_limit, spent, reserved, auto_approve_under) -> Decision:
    remaining = weekly_limit - spent - reserved
    if amount > remaining:        return Decision("over_budget", over_by=amount - remaining)   # pending + over-budget text
    if amount < auto_approve_under: return Decision("auto_approve")                             # approved now
    return Decision("needs_approval")                                                          # pending + approval text
```

- "Under" auto-approve is strict `<`: a $10 purchase with auto-approve under $10 needs a text.
- Round money to cents. Reject `amount <= 0` with `error`.
- Order of checks inside `request_spend`:
  1. Validate input.
  2. Lock the user.
  3. Expire stale pending requests.
  4. Pending exists → `busy` (or the idempotent same request).
  5. Match the category.
  6. `decide()`.
  7. Insert.
  8. Commit.
  9. Notify if pending.

## Concurrency design (D8)

Two layers:

1. **Short critical section (atomic check-and-insert).** All of `request_spend` steps 2–8 run in ONE Postgres transaction, starting with:

   ```sql
   select id, phone from users where connector_key = %s for update;
   ```

   This row lock serializes concurrent `request_spend` calls for the same user, whether from two Claude windows or two agents. The second call waits a few ms, then sees the first one's pending row and returns `busy`. It also stops two auto-approvals from both passing the budget check and overspending.
2. **Long lease (the pending period).** The `pending` row itself is the lock. The DB enforces it too, belt and braces:

   ```sql
   create unique index one_pending_per_user on requests (user_id) where status = 'pending';
   ```

   If an insert ever hits a unique violation (`psycopg.errors.UniqueViolation`), return `busy`.

- **Why not a separate agent-key table:** every agent instance connects with the same link, so per-agent identity isn't reliable. Stateless HTTP has no session id, and the request has no stable client id. The pending request's id is a natural key: only the agent that created it holds it. It frees itself on Y, N or expiry, so nothing can get stuck for more than 10 min.
- **Person 3's reply webhook** updates with `where id = ... and status = 'pending'`. A Y racing an expiry can't both win.

`busy` response example:

```json
{"status": "busy", "message": "Another purchase is waiting for the user's approval: $29.00 at Notion (Software), sent 14:31. Wallie allows one purchase at a time. Tell the user and do not retry until it is approved, declined, or expires at 14:41."}
```

## Tools (exact names, params, descriptions)

Descriptions are prompts. Keep the wording; Claude's behaviour depends on it.

### `check_budget(category: str | None = None, amount: float | None = None)`

> Check the user's Wallie spending allowance (test dollars). Returns each budget category with its weekly limit, spent in the last 7 days, reserved by pending requests, remaining, and the auto-approve threshold. Pass an amount (and category) to preview whether a purchase would be auto-approved, need the user's text approval, or be over budget. Use the exact category names returned here when calling request_spend.

Returns `{categories: [{category, weekly_limit, spent, reserved, remaining, auto_approve_under}], weekly_budget, pending_request: {...} | null, preview?: {outcome, over_by?}}`.

### `request_spend(amount: float, category: str, merchant: str, description: str, url: str | None = None)`

> Always call request_spend before any purchase. Asks Wallie, the user's spending allowance, for permission to spend. Never complete a purchase yourself: once approved, give the user the purchase link and let them buy it. Results: "approved" → confirm and share the link. "pending" → tell the user Wallie has texted them for approval, then call get_status with the request_id until it is no longer pending. "busy" → another purchase is already waiting for approval; tell the user and do not retry until it resolves. "declined" or "expired" → do not buy; tell the user. "error" → read the message (for example, use one of the listed categories).

Returns `{status: approved|pending|busy|error, request_id?, amount, category, merchant, remaining?, over_by?, expires_at?, message}`. `message` is a plain-English instruction for Claude, e.g. "Approved. Share the link with the user; they complete the purchase themselves."

### `get_status(request_id: str | None = None)`

> Check a Wallie spend request. Waits up to about 25 seconds for the user's text reply before returning. If still "pending", tell the user you're still waiting for their reply, then call get_status again. "approved" → confirm and share the purchase link. "declined" or "expired" → do not buy and tell the user.

Returns `{request_id, status, amount, category, merchant, url, created_at, decided_at, expires_at, message}`. Applies lazy expiry before reading.

## Architecture

```
Claude app / Claude Code
   │  Streamable HTTP  https://<domain>/mcp/<connector_key>
   ▼
server.py  ── KeyFromPath ASGI middleware: rewrites /mcp/<key> → /mcp, adds header x-wallie-key
   │          tools read the key via fastmcp.server.dependencies.get_http_request().headers
   ├─ rules.py   pure decide()
   ├─ db.py      psycopg AsyncConnectionPool(DATABASE_URL); transactional request_spend, status reads, lazy expiry
   └─ notify.py  httpx POST NOTIFY_URL (5 s timeout) or console print in dev
                    │
Supabase Postgres ◄─┘        Person 3's service ── Twilio SMS ── user's phone ── Y/N ──► Person 3 webhook ──► Postgres
```

`server.py` sketch (verify the fastmcp API in step 1, since versions differ):

```python
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request

mcp = FastMCP("Wallie")

def connector_key() -> str:
    return get_http_request().headers.get("x-wallie-key", "")

class KeyFromPath:  # /mcp/<key>[/] -> /mcp, key -> header
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            parts = [p for p in scope["path"].split("/") if p]
            if len(parts) == 2 and parts[0] == "mcp":
                headers = [(k, v) for k, v in scope["headers"] if k != b"x-wallie-key"]
                headers.append((b"x-wallie-key", parts[1].encode()))
                scope = {**scope, "path": "/mcp", "raw_path": b"/mcp", "headers": headers}
        await self.app(scope, receive, send)

app = KeyFromPath(mcp.http_app(path="/mcp", stateless_http=True))  # run: uv run uvicorn server:app --port 8000
```

- If `http_app` doesn't accept `stateless_http`, check `help(mcp.http_app)` or set it on `FastMCP(...)`.
- If `/mcp` redirects to `/mcp/`, rewrite to whichever path the app serves.
- The Starlette lifespan must still reach the inner app. The wrapper passes non-http scopes through unchanged.

## Files to create (only inside `mcp-server/`)

```
mcp-server/
  PLAN.md                    this file
  pyproject.toml             uv project (python 3.12)
  .env.example               DATABASE_URL, NOTIFY_URL, NOTIFY_SECRET, PORT, MCP_BASE_URL
  .gitignore                 .env, .venv/, __pycache__/
  server.py                  FastMCP app, 3 tools, KeyFromPath
  rules.py                   decide()
  db.py                      pool + queries + transactions
  notify.py                  send_approval_text(payload)
  sql/schema_additions.sql   url column + 2 unique indexes (copy of TEAM-CONTRACT; Person 2 runs it)
  sql/seed_demo.sql          demo user (connector_key 'demo') + Software envelope 75 / 10
  scripts/smoke.py           fastmcp Client: list tools, check_budget, request_spend $5 + $29, get_status
  scripts/race.py            fires 2 request_spend concurrently → expect exactly 1 pending + 1 busy
  scripts/reply.py           dev stand-in for Person 3: python scripts/reply.py y|n  (updates the user's pending row)
  scripts/reset_demo.py      deletes the demo user's requests so the demo can rerun
  tests/test_rules.py        5 rule rows + boundary ($10 vs under $10) + over_by math
```

**Env** (`mcp-server/.env`, never committed):
- `DATABASE_URL`: Supabase session pooler string. If you use the transaction pooler on port 6543, pass `prepare_threshold=None` to psycopg.
- `NOTIFY_URL`, `NOTIFY_SECRET`: from Person 3. Leave empty for console mode.
- `PORT=8000`
- `MCP_BASE_URL`: the ngrok domain.

**Key SQL** for the per-envelope budget (one query):

```sql
select e.id, e.category, e.weekly_limit, e.auto_approve_under,
  coalesce(sum(r.amount) filter (where r.status = 'approved' and r.created_at > now() - interval '7 days'), 0) as spent,
  coalesce(sum(r.amount) filter (where r.status = 'pending'), 0) as reserved
from envelopes e left join requests r on r.envelope_id = e.id
where e.user_id = %s
group by e.id;
```

**Lazy expiry:**

```sql
update requests set status = 'expired', decided_at = now()
where user_id = %s and status = 'pending' and created_at < now() - interval '10 minutes';
```

## Build steps

Times are targets. If a step runs 10+ min over, tell the user and cut scope: drop stretch items first, then the concurrency script (never the lock itself).

- [ ] **0. Contract out (by 14:05).** User sends `docs/TEAM-CONTRACT.md` to the team. *(Done by the user; confirm with them.)*
- [x] **1. Spike (14:05–14:20).**
  1. `uv init --app --python 3.12` in `mcp-server/`.
  2. `uv add fastmcp "psycopg[binary,pool]" httpx python-dotenv uvicorn` and `uv add --dev pytest`.
  3. Write `server.py` with a temporary `whoami` tool that returns the key from the path.
  4. Run `uv run uvicorn server:app --port 8000`.
  5. Prove the key arrives via `scripts/smoke.py`, using fastmcp `Client("http://localhost:8000/mcp/demo")`. This de-risks routing before any DB work.
- [x] **2. Rules (14:20–14:30).** `rules.py` + `tests/test_rules.py`; `uv run pytest` green.
- [x] **3. DB (14:30–14:50).** *(code done; unverified against a real database — waiting on `DATABASE_URL`)*
  1. `db.py` per D12: RPC adapter (`check_budget`, `request_spend`, `get_status`) over a psycopg pool, cents↔dollars conversion, `blocked`→`busy` + idempotent-retry check, exception→`error` mapping.
  2. No `sql/seed_demo.sql` — seeding goes through `auth.users`, which only Person 2's side can do cleanly. Dropped; see queue instead.
  3. Needs `DATABASE_URL` + a demo connector key from Person 2 (see queue).
- [x] **4. Tools (14:50–15:00).** *(code done; smoke/race unrun — waiting on `DATABASE_URL`)*
  1. Implement the 3 tools per **Tools**. Remove `whoami`.
  2. `notify.py` in console mode.
  3. `scripts/smoke.py` exercises: $5 (auto-approve), $29 (pending), a second $12 (`busy`), then `scripts/reply.py y` → `get_status` shows `approved`.
  4. Run `scripts/race.py`.
- [ ] **5. Claude Code (by 15:05).**
  1. `claude mcp add --transport http wallie http://localhost:8000/mcp/demo`.
  2. In a Claude Code session: "Buy me a $29 Notion subscription", with a reply via `scripts/reply.py`.
  3. Confirm Claude calls `request_spend`, waits via `get_status`, then shares the link.
  4. Tick the spec's "MCP connected in Claude Code".
- [ ] **6. Public URL + Claude app (by 15:15).**
  1. `winget install ngrok.ngrok`, then `ngrok config add-authtoken <token>`, then `ngrok http --url=<static-domain> 8000`.
  2. In claude.ai: Settings → Connectors → Add custom connector → `https://<static-domain>/mcp/demo`. Run the demo prompt.
  3. Send `MCP_BASE_URL` to Person 1.
  4. Tick "MCP connected in Claude app".
- [ ] **7. Real texts (by 15:35).**
  1. Put `NOTIFY_URL` + `NOTIFY_SECRET` in `.env`.
  2. Run `$29` → real SMS → reply Y on the phone → Claude shares the link.
  3. Test an over-budget case and an N.
- [ ] **8. Integration (by 15:45).**
  1. Create a user through Person 1's setup flow.
  2. Connect with *their* generated link and command.
  3. Run `scripts/reset_demo.py`, then do a full demo dry run with Patrick.
- [ ] **Stretch (only if all above are green).** `cancel_request(request_id)` tool to release the lock early. A Railway deploy. A friendlier `check_budget` summary line.

## Dependency queue (tell the user at these moments)

When you reach the trigger, stop and give the user the "ask" text to pass on. If it's late, take the fallback and keep building.

| Trigger | Ask (from whom) | Why | Fallback if late |
| --- | --- | --- | --- |
| Start of step 1 | Confirm `TEAM-CONTRACT.md` was sent to Persons 1, 2 and 3 (user) | Everyone builds against it | Resend the relevant section |
| Start of step 3 (~14:30) | "Person 2: please send the Supabase session pooler connection string (project `ucsysxhiydfgukkkbtdx`) privately." | DB access | Ask the user to create a temporary Supabase project with the same migrations applied; swap `DATABASE_URL` later |
| Step 3 seed | "Person 2: please create a demo user (phone = whoever gets the demo texts), set their Software envelope to weekly_limit_cents 7500 / auto_approve_under_cents 1000, call rotate_connector_key() for them, and send the key." | Need a real connector key + seeded envelope to test against; we can't create `auth.users` rows ourselves | Use the user's own phone/account once Person 1's setup flow is live |
| Start of step 6 (~15:05) | "Please sign up at ngrok.com, then give me the authtoken and your free static domain." (user action, ~3 min) | Stable public URL for the Claude app | `cloudflared tunnel --url http://localhost:8000` (URL changes per restart; Claude Code works meanwhile) |
| End of step 6 (~15:15) | Tell Person 1: "`MCP_BASE_URL` = `https://<static-domain>`" | Connect screen links | — |
| Start of step 7 (~15:15) | "Person 3: please send `NOTIFY_URL` + `NOTIFY_SECRET`, and confirm the reply webhook is live." | Real SMS | Keep console mode + `scripts/reply.py` for the demo; record the SMS part later |
| Start of step 8 (~15:35) | "Person 1: is setup creating users + envelopes + connector links? Send me a test link." / "Patrick: ready for a dry run?" | Integration | Demo with the seeded `demo` user |

## Working rules

- Work only in `mcp-server/` (plus ticking boxes in `docs/SPEC.md`). Never edit other teams' folders.
- Commit after every step: `git pull --rebase origin main`, then commit, then `git push origin main`. Use a short message (`mcp: step 3 db layer`) with the `Co-Authored-By` trailer from the session's instructions. Never commit `.env`.
- The shell is Windows. The Bash tool is Git Bash, and PowerShell is available. `uv` is at `C:\Users\jonss\.local\bin\uv.exe`.
- Keep it simple: no ORM, no auth beyond the key, no background jobs. If something isn't on the demo path, it waits.
- Report to the user in short status lines: what's done, what's next, and anything you need from them.

## Log

- 13:42 Repo cloned, spec saved to `docs/SPEC.md`.
- 13:59 Decisions locked with the user:
  - Python and the connector link format approved.
  - Categories are free text.
  - Wally dropped.
  - One-purchase-in-flight lock (agent key = pending `request_id`).
  - Wrote `docs/TEAM-CONTRACT.md` and this handoff plan.
- 14:07 Step 1 done (Linux, not Windows — adapted paths, no winget/uv.exe). `uv init --app`, flattened to a plain `mcp-server/` layout (no `src/`). Confirmed fastmcp 4.1.0's `http_app(stateless_http=...)` and `get_http_request` match the plan's sketch exactly, no changes needed. `server.py` with `whoami` + `KeyFromPath` running under `uv run uvicorn server:app --port 8000`; `scripts/smoke.py` confirms `/mcp/demo` → `whoami` returns `{"connector_key": "demo"}`. Asked the user to confirm `TEAM-CONTRACT.md` went to Persons 1/2/3 (step 0, still open).
- 14:13 GitHub push auth set up (`gh auth login --web`, user tjor06). Pushed step 1.
- 14:16 Step 2 done: `rules.py` (pure `decide()`, `Decimal` cents rounding) + `tests/test_rules.py` (7 cases: auto-approve, needs-approval, over-budget with `over_by`, spent+reserved interaction, exact-remaining boundary, strict-`<` auto-approve boundary). Added `pytest.ini` (`pythonpath = .`) so flat-layout tests import root modules. `uv run pytest` green.
- 14:38 Steps 3+4 **code** done, but **nothing is verified against a real database yet** — still no `DATABASE_URL`. Written: `db.py` (RPC adapter per D12, pool with `prepare_threshold=None` on port 6543, `RpcError` mapping, `latest_request_id` for D9's default, `resolve_reply` for dev), `notify.py` (httpx POST with 5 s timeout, console mode when `NOTIFY_URL` unset), all three tools in `server.py` with the plan's exact descriptions, and `scripts/{smoke,race,reply,reset_demo}.py` + `scripts/dev.sh`. Verified without a DB: tools register with the right names/params, and every DB-dependent path degrades to a Claude-readable `error` instead of crashing. **Unverified:** every actual RPC call, cents↔dollars mapping against real rows, the `blocked`→`busy` path, D8 idempotent retry, and the long-poll. Two notes for the next instance: (1) I dropped a `get_status` fallback that probed for the pending id by calling `request_spend` — it could insert a junk $0.01 row on a race; it now reads `spend_requests` directly. (2) The over-budget case returns `pending` from the RPC, so our `request_spend` reports `pending` with `over_by` set, not a distinct status.
- 14:20 Person 2's database branch merged (`supabase/migrations/`) — bigger than D6 asked for: budget math, the lock, and the decision are RPCs (`check_budget`/`request_spend`/`get_status`/`resolve_reply`), money is integer cents, categories are a hard-constrained fixed four with no `url` column. Conflicted with D7 (free text, already sent to Person 1 in the contract). Asked the user; decision: adopt Person 2's schema as-is, fixed four categories. Superseded D6/D7/D10, added D12 (db.py is an RPC adapter). Corrected `docs/TEAM-CONTRACT.md` and `docs/SPEC.md`. Still need from Person 2: `DATABASE_URL` and a seeded demo user + connector key (users are created via an `auth.users` trigger, not a raw insert, so we can't seed one ourselves).
