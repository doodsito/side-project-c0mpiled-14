"""Checks the request_spend RPC agrees with rules.py.

The decision lives in SQL, so rules.py could drift from it unnoticed. These cases run
against a real database and compare the two. Skipped when DATABASE_URL is unset.

Run after:  uv run python scripts/init_db.py
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import db  # noqa: E402
from rules import decide  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL", "").strip(), reason="no DATABASE_URL"
)

KEY = os.environ.get("DEMO_KEY") or "demo"

# (amount, expected rules.py outcome) against the seeded software envelope:
# weekly_limit 75, auto_approve_under 10, allowance 200.
CASES = [
    (5, "auto_approve"),
    (9.99, "auto_approve"),
    (10, "needs_approval"),  # strict <: exactly the threshold needs a text
    (29, "needs_approval"),
    (75, "needs_approval"),  # exactly the limit is not over budget
    (90, "over_budget"),
]

# How the RPC reports each rules.py outcome.
EXPECTED_DECISION = {
    "auto_approve": "approved",
    "needs_approval": "pending",
    "over_budget": "pending",
}


async def _clear():
    await db.delete_requests(KEY)


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.mark.parametrize("amount,expected", CASES)
def test_rpc_matches_rules(amount, expected):
    async def go():
        await _clear()
        budget = await db.check_budget(KEY)
        env = next(e for e in budget["envelopes"] if e["category"] == "software")

        local = decide(
            amount=amount,
            weekly_limit=env["weekly_limit_cents"] / 100,
            spent=env["spent_cents"] / 100,
            reserved=0,
            auto_approve_under=env["auto_approve_under_cents"] / 100,
        )
        assert local.outcome == expected, f"rules.py changed: {amount} -> {local.outcome}"

        raw = await db.request_spend(
            KEY, int(round(amount * 100)), "software", "ParityTest", "parity"
        )
        assert raw["decision"] == EXPECTED_DECISION[expected], (
            f"SQL and rules.py disagree on ${amount}: rules.py says {local.outcome} "
            f"(-> {EXPECTED_DECISION[expected]}), SQL said {raw['decision']}"
        )

        if expected == "over_budget":
            assert raw["over_by_cents"] > 0, "SQL did not flag this as over budget"
            assert raw["over_by_cents"] == int(round(local.over_by * 100)), (
                f"over_by differs: rules.py {local.over_by}, SQL {raw['over_by_cents'] / 100}"
            )
        else:
            assert raw["over_by_cents"] == 0

        await _clear()

    _run(go())
