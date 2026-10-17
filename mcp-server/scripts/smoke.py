"""Smoke test against a running server: uv run python scripts/smoke.py [base_url]"""

import asyncio
import sys

from fastmcp import Client

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/mcp/demo"


async def main():
    async with Client(BASE_URL) as client:
        tools = await client.list_tools()
        print("tools:", [t.name for t in tools])

        result = await client.call_tool("whoami", {})
        print("whoami:", result.data)


if __name__ == "__main__":
    asyncio.run(main())
