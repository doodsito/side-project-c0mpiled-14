"""Fires two request_spend calls at once: expect exactly one pending, one busy.

uv run python scripts/race.py [connector_key] [base]

Clears the user's existing requests first, so a leftover pending one from an earlier
run cannot turn both racers into "busy".
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastmcp import Client

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import db  # noqa: E402

KEY = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DEMO_KEY", "demo")
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8000"
URL = f"{BASE}/mcp/{KEY}"


async def fire(merchant: str, amount: float):
    async with Client(URL) as client:
        result = await client.call_tool(
            "request_spend",
            {
                "amount": amount,
                "category": "software",
                "merchant": merchant,
                "description": f"race test {merchant}",
            },
        )
        return result.data


async def main():
    cleared = await db.delete_requests(KEY)
    if cleared:
        print(f"(cleared {cleared} existing request(s) for {KEY!r})")

    a, b = await asyncio.gather(fire("Figma", 29), fire("Linear", 31))
    print("A:", a.get("status"), a.get("merchant"))
    print("B:", b.get("status"), b.get("merchant"))

    statuses = sorted([a.get("status"), b.get("status")])
    if statuses == ["busy", "pending"]:
        print("\nPASS: exactly one pending, one busy")
    else:
        print(f"\nFAIL: expected ['busy', 'pending'], got {statuses}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
