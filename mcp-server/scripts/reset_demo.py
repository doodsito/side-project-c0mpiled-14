"""Clears the demo user's spend requests so the demo can rerun.

uv run python scripts/reset_demo.py [connector_key]
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

import db  # noqa: E402

KEY = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DEMO_KEY", "demo")


async def main():
    deleted = await db._call(
        """
        with gone as (
          delete from public.spend_requests r
          using public.profiles p
          where p.id = r.user_id and p.connector_key_hash = private.hash_key(%s)
          returning 1
        )
        select count(*) from gone
        """,
        (KEY,),
    )
    print(f"deleted {deleted} request(s)")


if __name__ == "__main__":
    asyncio.run(main())
