# Wallie MCP server

Gives Claude a weekly spending allowance in test dollars. Claude must ask before
buying: small amounts are auto-approved, bigger ones text the user, who replies Y or N.
Exposes three MCP tools over Streamable HTTP — `check_budget`, `request_spend`,
`get_status`.

Runs on macOS, Linux and Windows. No money moves; all amounts are test dollars.

## 1. Install uv

Python 3.12+ is managed by [uv](https://docs.astral.sh/uv/), so that is the only
prerequisite.

```sh
# macOS (either one)
brew install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then, from this `mcp-server/` directory:

```sh
uv sync
```

That creates `.venv` and installs the locked dependencies. You never need to activate
it — every command below starts with `uv run`.

## 2. Point it at a database

```sh
cp .env.example .env
```

Fill in `DATABASE_URL`. Pick whichever applies:

**A. The team's Supabase project** (what the demo uses). Supabase dashboard → Connect →
Session pooler → copy the URI. It must be the `postgres` / `service_role` user: the
RPCs and tables are granted to `service_role` only, so the publishable/anon key gets
"permission denied".

```
DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
```

> Using the **transaction** pooler on port `6543` instead? That is handled — the code
> disables prepared statements automatically when it sees `:6543`.

**B. A local Postgres**, for working offline without the team's credentials. Needs
Docker:

```sh
docker compose up -d
# then in .env:
# DATABASE_URL=postgresql://postgres:wallie@localhost:5433/wallie
```

Now create the schema and a demo user:

```sh
uv run python scripts/init_db.py                 # fresh local database
uv run python scripts/init_db.py --seed-only     # the team's Supabase, schema already there
```

On a fresh local database this applies `sql/local_dev_shim.sql` — the pieces Supabase
provides (the `auth` schema, pgcrypto, the `anon`/`authenticated`/`service_role` roles)
— and then the real migration from `supabase/migrations/`, unchanged. The schema's
single source of truth stays in `supabase/`; nothing is duplicated here.

Defaults to connector key `demo`, a $200/week allowance and a `software` envelope of
$75/week auto-approving under $10 — the demo setup. Override with `--key`, `--phone`,
`--category`, `--allowance`, `--limit`, `--auto`.

## 3. Run it

```sh
uv run uvicorn server:app --port 8000
```

`scripts/dev.sh` does the same thing but kills any previous instance first (macOS and
Linux). The connector URL is `http://localhost:8000/mcp/<connector_key>`, so with the
demo seed: `http://localhost:8000/mcp/demo`.

## 4. Connect Claude

**Claude Code** works against localhost:

```sh
claude mcp add --transport http wallie http://localhost:8000/mcp/demo
```

`--transport http` is required — without it Claude Code tries to execute the URL as a
program.

**The Claude app** reaches the server from Anthropic's cloud, so it needs a public
HTTPS URL. Expose the local port with a tunnel:

```sh
ngrok http 8000                  # or: ngrok http --url=<your-static-domain> 8000
cloudflared tunnel --url http://localhost:8000   # no account needed, URL changes per restart
```

Then in claude.ai: Settings → Connectors → Add custom connector → `https://<domain>/mcp/demo`.

Add this to the project instructions so Claude reaches for the tools:

> Before any purchase, check with Wallie.

## 5. Texts

With `NOTIFY_URL` empty, approval texts print to the console instead of being sent, and
you reply on the user's behalf:

```sh
uv run python scripts/reply.py y      # or n
```

With `NOTIFY_URL` and `NOTIFY_SECRET` set, the server POSTs the approval to the SMS person's
service, which sends the real SMS; the user's Y/N reply comes back through that
service's webhook.

## Checking it works

```sh
uv run pytest                           # approval rules + SQL parity (parity needs a database)
uv run python scripts/smoke.py          # full path: $5 auto-approve, $29 pending, busy, idempotent retry
uv run python scripts/race.py           # two concurrent requests -> exactly one pending, one busy
uv run python scripts/reset_demo.py     # clear the demo user's requests to rerun
```

`smoke.py` and `race.py` need the server running and take an optional connector key and
base URL: `uv run python scripts/smoke.py mykey https://my-domain.ngrok-free.app`.

## How it decides

The decision is made in SQL by the `request_spend` RPC. `rules.py` is the same logic as
a pure reference implementation, and `tests/test_sql_parity.py` asserts the two agree
whenever a database is configured, so they cannot drift apart unnoticed.

| Situation | Result |
| --- | --- |
| Amount is over what's left in the envelope | `pending` + an over-budget text ("Over by $X. Approve anyway?") |
| Strictly below the envelope's auto-approve amount | `approved` immediately |
| Anything else in budget | `pending` + an approval text |

Remaining is `weekly_limit − spent`, where spent is approved requests from the last 7
days (rolling, no reset job); the amount is checked against both the envelope's limit
and the account's weekly allowance. A $10 purchase with auto-approve under $10 needs a
text — the comparison is strict.

Money is integer cents in the database. The tools speak dollars, converting at the
boundary. Categories are the fixed four — `food`, `travel`, `shopping`, `software` —
created for every user; matching is case-insensitive and trimmed.

**One purchase in flight per user.** A pending request *is* the lock, and its
`request_id` is the key the requesting agent holds. While it exists, any other
`request_spend` for that user returns `busy`, even for amounts that would otherwise be
auto-approved; the exception is a retry with the same amount and merchant, which gets
the existing request back instead. Two layers enforce it: the RPC takes a `for update`
row lock on the user's profile for its whole transaction, and a partial unique index
(`spend_requests_one_pending_idx`) is the backstop. The lock releases on Y, N, or the
10-minute expiry, which is applied lazily by whichever RPC next touches the row — there
is no cron.

## Layout

```
server.py            the three MCP tools + KeyFromPath middleware
rules.py             reference implementation of the rules (not called at runtime)
db.py                psycopg pool; calls the RPCs, plus the few reads that have none
notify.py            POSTs the approval to the SMS person, or prints it in dev
sql/                 local_dev_shim.sql: what Supabase provides, for a plain Postgres
scripts/             init_db, smoke, race, reply, reset_demo, dev.sh
tests/               approval rules + SQL parity
PLAN.md              build plan, decisions and running log
```

The connector key arrives in the URL path. `KeyFromPath` rewrites `/mcp/<key>` to
`/mcp` and moves the key into an `x-wallie-key` header, which the tools read via
`get_http_request()`. That key is the only authentication, so connector links must not
be guessable and must not be shared.
