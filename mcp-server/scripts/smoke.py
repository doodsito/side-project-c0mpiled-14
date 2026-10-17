"""Walks the demo path against a running server.

uv run python scripts/smoke.py [connector_key] [base]
"""

import asyncio
import os
import sys

from dotenv import load_dotenv
from fastmcp import Client

load_dotenv()

KEY = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DEMO_KEY", "demo")
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8000"
URL = f"{BASE}/mcp/{KEY}"


def show(label, data):
    print(f"\n--- {label} ---")
    print(data)


async def main():
    async with Client(URL) as client:
        tools = await client.list_tools()
        show("tools", [t.name for t in tools])

        show("check_budget", (await client.call_tool("check_budget", {})).data)

        show(
            "check_budget preview $29 software",
            (await client.call_tool("check_budget", {"category": "software", "amount": 29})).data,
        )

        show(
            "request_spend $5 (expect approved)",
            (
                await client.call_tool(
                    "request_spend",
                    {
                        "amount": 5,
                        "category": "software",
                        "merchant": "Raycast",
                        "description": "Raycast Pro, 1 month",
                        "url": "https://raycast.com/pro",
                    },
                )
            ).data,
        )

        pending = (
            await client.call_tool(
                "request_spend",
                {
                    "amount": 29,
                    "category": "software",
                    "merchant": "Notion",
                    "description": "Notion Plus, 1 month",
                    "url": "https://notion.so/pricing",
                },
            )
        ).data
        show("request_spend $29 (expect pending)", pending)

        show(
            "request_spend $12 other merchant (expect busy)",
            (
                await client.call_tool(
                    "request_spend",
                    {
                        "amount": 12,
                        "category": "software",
                        "merchant": "Linear",
                        "description": "Linear Standard",
                    },
                )
            ).data,
        )

        show(
            "request_spend $29 Notion again (expect idempotent pending)",
            (
                await client.call_tool(
                    "request_spend",
                    {
                        "amount": 29,
                        "category": "software",
                        "merchant": "Notion",
                        "description": "Notion Plus, 1 month",
                    },
                )
            ).data,
        )

        print("\nNow run:  uv run python scripts/reply.py y")
        print("then re-run get_status, or let the poll below catch it.\n")
        show(
            "get_status (long-polls up to 25s)",
            (await client.call_tool("get_status", {"request_id": pending.get("request_id")})).data,
        )


if __name__ == "__main__":
    asyncio.run(main())
