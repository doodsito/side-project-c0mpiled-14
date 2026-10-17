"""The approval rules, as a pure reference implementation.

NOTE: the server does not call this at runtime. Person 2's `request_spend` RPC makes
the decision in SQL (see supabase/migrations/). This module is kept as the executable
statement of what those rules are, and tests/test_rules.py checks the SQL agrees with
it whenever DATABASE_URL is set — so the two cannot drift apart unnoticed.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass
class Decision:
    outcome: str  # auto_approve | needs_approval | over_budget
    over_by: Decimal | None = None


def _cents(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def decide(amount, weekly_limit, spent, reserved, auto_approve_under) -> Decision:
    amount = _cents(amount)
    weekly_limit = _cents(weekly_limit)
    spent = _cents(spent)
    reserved = _cents(reserved)
    auto_approve_under = _cents(auto_approve_under)

    remaining = weekly_limit - spent - reserved
    if amount > remaining:
        return Decision("over_budget", over_by=amount - remaining)
    if amount < auto_approve_under:
        return Decision("auto_approve")
    return Decision("needs_approval")
