"""Clears spend requests so the demo can rerun from a clean slate.

  uv run python scripts/reset_demo.py --all           # every user (simplest)
  uv run python scripts/reset_demo.py --phone +1650...
  uv run python scripts/reset_demo.py <connector_key>
  uv run python scripts/reset_demo.py                 # DEMO_KEY from .env

Use --all when you signed up through the website and don't have the connector key
to hand: these are test dollars and the only rows are demo purchases.
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import db  # noqa: E402


async def main():
    args = sys.argv[1:]

    if "--all" in args:
        print(f"deleted {await db.delete_all_requests()} request(s) across all users")
        return

    if "--phone" in args:
        i = args.index("--phone")
        if i + 1 >= len(args):
            sys.exit("--phone needs a number, e.g. --phone +16505551234")
        phone = args[i + 1]
        print(f"deleted {await db.delete_requests_by_phone(phone)} request(s) for {phone}")
        return

    key = args[0] if args and not args[0].startswith("-") else (os.environ.get("DEMO_KEY") or "")
    if not key:
        sys.exit(
            "No connector key. Pass one, set DEMO_KEY in .env, or use --all / --phone +1..."
        )
    print(f"deleted {await db.delete_requests(key)} request(s) for that connector key")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except db.DbError as exc:
        sys.exit(f"error: {exc.message}")
