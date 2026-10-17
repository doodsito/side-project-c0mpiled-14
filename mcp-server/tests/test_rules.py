from decimal import Decimal

from rules import decide


def test_auto_approve_well_under_threshold():
    d = decide(amount=5, weekly_limit=75, spent=0, reserved=0, auto_approve_under=10)
    assert d.outcome == "auto_approve"


def test_needs_approval_in_budget_over_threshold():
    d = decide(amount=29, weekly_limit=75, spent=0, reserved=0, auto_approve_under=10)
    assert d.outcome == "needs_approval"


def test_over_budget():
    d = decide(amount=90, weekly_limit=75, spent=0, reserved=0, auto_approve_under=10)
    assert d.outcome == "over_budget"
    assert d.over_by == Decimal("15.00")


def test_spent_and_reserved_reduce_remaining():
    # remaining = 75 - 50 - 20 = 5; amount 6 > remaining 5 -> over_budget by 1
    d = decide(amount=6, weekly_limit=75, spent=50, reserved=20, auto_approve_under=10)
    assert d.outcome == "over_budget"
    assert d.over_by == Decimal("1.00")


def test_exact_remaining_is_not_over_budget():
    # remaining = 75 - 50 - 20 = 5; amount 5 == remaining -> not over_budget
    d = decide(amount=5, weekly_limit=75, spent=50, reserved=20, auto_approve_under=10)
    assert d.outcome in ("auto_approve", "needs_approval")


def test_boundary_equal_to_auto_approve_under_needs_approval():
    # strict <: a $10 purchase with auto_approve_under 10 needs approval, not auto-approved
    d = decide(amount=10, weekly_limit=75, spent=0, reserved=0, auto_approve_under=10)
    assert d.outcome == "needs_approval"


def test_boundary_just_under_auto_approve_under_is_auto_approved():
    d = decide(amount=9.99, weekly_limit=75, spent=0, reserved=0, auto_approve_under=10)
    assert d.outcome == "auto_approve"
