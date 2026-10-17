"""Dev stand-in for Person 3's reply webhook.

uv run python scripts/reply.py y|n [phone]
Phone defaults to DEMO_PHONE in .env.
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

import db  # noqa: E402


async def main():
    if len(sys.argv) < 2 or sys.argv[1].strip().lower() not in ("y", "n", "yes", "no"):
        sys.exit("usage: reply.py y|n [phone]")
    phone = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("DEMO_PHONE", "").strip()
    if not phone:
        # Nothing configured: if exactly one user is waiting, reply for them.
        phone = await db.phone_awaiting_reply()
    if not phone:
        sys.exit(
            "Could not tell who to reply for: no pending request, or more than one user has "
            "one. Pass the phone as the 2nd argument, or set DEMO_PHONE in .env"
        )

    print(await db.resolve_reply(phone, sys.argv[1]))


if __name__ == "__main__":
    asyncio.run(main())
