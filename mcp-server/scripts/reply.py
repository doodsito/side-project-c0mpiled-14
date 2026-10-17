"""Dev stand-in for Person 3's webhook: uv run python scripts/reply.py y|n [phone]"""

import asyncio
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

import db  # noqa: E402


async def main():
    if len(sys.argv) < 2 or sys.argv[1].lower() not in ("y", "n", "yes", "no"):
        sys.exit("usage: reply.py y|n [phone]")
    reply = sys.argv[1]
    phone = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("DEMO_PHONE", "")
    if not phone:
        sys.exit("No phone given. Pass one, or set DEMO_PHONE in .env")

    print(await db.resolve_reply(phone, reply))


if __name__ == "__main__":
    asyncio.run(main())
