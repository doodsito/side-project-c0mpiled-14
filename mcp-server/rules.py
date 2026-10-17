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
