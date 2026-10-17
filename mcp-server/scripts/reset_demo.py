"""Clears a user's spend requests so the demo can rerun.

uv run python scripts/reset_demo.py [connector_key]
Key defaults to DEMO_KEY in .env, then "demo".
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

import db  # noqa: E402


async def main():
    key = sys.argv[1] if len(sys.argv) > 1 else (os.environ.get("DEMO_KEY") or "demo")
    print(f"deleted {await db.delete_requests(key)} request(s) for key {key!r}")


if __name__ == "__main__":
    asyncio.run(main())
