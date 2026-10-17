"""Builds the schema and a demo user in whatever DATABASE_URL points at.

Local/scratch Postgres: applies sql/local_dev_shim.sql (the bits Supabase provides)
and then the real migration from supabase/migrations/, unchanged.

The team's Supabase project already has the schema, so use --seed-only there.

  uv run python scripts/init_db.py                    # schema + demo user
  uv run python scripts/init_db.py --seed-only        # against the real project
  uv run python scripts/init_db.py --key abc --phone +15105551234
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import db  # noqa: E402

SHIM = ROOT / "sql" / "local_dev_shim.sql"
MIGRATIONS = REPO / "supabase" / "migrations"
# The schema is the result of replaying the migrations; locally only the latest
# full-schema one is needed, since each supersedes the one before it.
SCHEMA_MIGRATION = "20261017214205_restore_rpc_schema.sql"

# Mirrors supabase/seed_demo.sql, with the phone and key as parameters. The trigger on
# auth.users creates the profile and the four envelopes.
SEED_USER = """
insert into auth.users (
  instance_id, id, aud, role, phone, phone_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at,
  confirmation_token, recovery_token, email_change_token_new, email_change,
  phone_change, phone_change_token, email_change_token_current, reauthentication_token
)
values (
  '00000000-0000-0000-0000-000000000000', %(id)s,
  'authenticated', 'authenticated', %(phone)s, now(),
  '{"provider": "phone", "providers": ["phone"]}', '{}', now(), now(),
  '', '', '', '', '', '', '', ''
)
on conflict (id) do update set phone = excluded.phone, phone_confirmed_at = now()
"""

SEED_LIMITS = """
update public.envelopes set weekly_limit_cents = %(limit)s, auto_approve_under_cents = %(auto)s
where user_id = %(id)s and category = %(category)s
"""

SEED_PROFILE = """
update public.profiles
set weekly_allowance_cents = %(allowance)s, connector_key_hash = private.hash_key(%(key)s)
where id = %(id)s
"""

DEMO_ID = "00000000-0000-4000-8000-00000000de30"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema-only", action="store_true")
    ap.add_argument("--seed-only", action="store_true", help="use against the real Supabase project")
    ap.add_argument("--key", default=os.environ.get("DEMO_KEY") or "demo")
    ap.add_argument("--phone", default=os.environ.get("DEMO_PHONE") or "+15555550100")
    ap.add_argument("--category", default="software", help="one of: food travel shopping software")
    ap.add_argument("--allowance", type=float, default=200, help="weekly allowance, dollars")
    ap.add_argument("--limit", type=float, default=75, help="envelope weekly limit, dollars")
    ap.add_argument("--auto", type=float, default=10, help="auto-approve under, dollars")
    ap.add_argument("--id", default=DEMO_ID)
    args = ap.parse_args()

    pool = db.pool()
    await pool.open()
    async with pool.connection() as conn:
        if not args.seed_only:
            await conn.execute(SHIM.read_text())
            await conn.execute((MIGRATIONS / SCHEMA_MIGRATION).read_text())
            print(f"schema applied: local_dev_shim.sql + {SCHEMA_MIGRATION}")

        if not args.schema_only:
            # auth.users stores the phone without the leading '+'; the trigger adds it back.
            params = {
                "id": args.id,
                "phone": args.phone.lstrip("+"),
                "category": args.category.strip().lower(),
                "limit": int(round(args.limit * 100)),
                "auto": int(round(args.auto * 100)),
                "allowance": int(round(args.allowance * 100)),
                "key": args.key,
            }
            await conn.execute(SEED_USER, params)
            await conn.execute(SEED_LIMITS, params)
            await conn.execute(SEED_PROFILE, params)
            print(
                f"demo user ready: key={args.key!r} phone={args.phone} "
                f"allowance ${args.allowance:.0f}/wk, {params['category']} ${args.limit:.0f}/wk "
                f"auto-approving under ${args.auto:.0f}"
            )
            print(f"connect with:  .../mcp/{args.key}")
    await pool.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except db.DbError as exc:
        sys.exit(f"error: {exc.message}")
