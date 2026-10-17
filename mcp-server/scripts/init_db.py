"""Creates the schema and a demo user in whatever DATABASE_URL points at.

Safe to re-run. Intended for a local/scratch Postgres; against the team's Supabase
project the schema already exists, so this only adds the demo user.

  uv run python scripts/init_db.py                      # schema + demo user
  uv run python scripts/init_db.py --schema-only
  uv run python scripts/init_db.py --key mykey --phone +15105551234
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import db  # noqa: E402

SCHEMA = ROOT / "sql" / "local_dev_schema.sql"

SEED = """
with u as (
  insert into public.users (phone, connector_key, weekly_budget)
  values (%(phone)s, %(key)s, %(budget)s)
  on conflict (connector_key) do update set phone = excluded.phone
  returning id
)
insert into public.envelopes (user_id, category, weekly_limit, auto_approve_under)
select id, %(category)s, %(limit)s, %(auto)s from u
on conflict (user_id, lower(category)) do update
  set weekly_limit = excluded.weekly_limit,
      auto_approve_under = excluded.auto_approve_under
"""


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema-only", action="store_true")
    ap.add_argument("--seed-only", action="store_true")
    ap.add_argument("--key", default=os.environ.get("DEMO_KEY") or "demo")
    ap.add_argument("--phone", default=os.environ.get("DEMO_PHONE") or "+15555550100")
    ap.add_argument("--category", default="Software")
    ap.add_argument("--budget", type=float, default=75)
    ap.add_argument("--limit", type=float, default=75)
    ap.add_argument("--auto", type=float, default=10)
    args = ap.parse_args()

    pool = db.pool()
    await pool.open()
    async with pool.connection() as conn:
        if not args.seed_only:
            await conn.execute(SCHEMA.read_text())
            print(f"schema applied from {SCHEMA.relative_to(ROOT)}")
        if not args.schema_only:
            await conn.execute(
                SEED,
                {
                    "phone": args.phone,
                    "key": args.key,
                    "budget": args.budget,
                    "category": args.category,
                    "limit": args.limit,
                    "auto": args.auto,
                },
            )
            print(
                f"demo user ready: key={args.key!r} phone={args.phone} "
                f"{args.category} ${args.limit:.0f}/wk, auto-approve under ${args.auto:.0f}"
            )
            print(f"connect with:  .../mcp/{args.key}")
    await pool.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except db.DbError as exc:
        sys.exit(f"error: {exc.message}")
