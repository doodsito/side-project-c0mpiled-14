import asyncio
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request

import db
import notify

load_dotenv()

mcp = FastMCP("Wallie")

CATEGORIES = ("food", "travel", "shopping", "software")
POLL_SECONDS = 1
POLL_TIMEOUT = 25


def connector_key() -> str:
    return get_http_request().headers.get("x-wallie-key", "")


def _dollars(cents) -> float:
    return round((cents or 0) / 100, 2)


def _cents(amount: float) -> int:
    return int(round(amount * 100))


def _clock(iso: str | None) -> str:
    if not iso:
        return ""
    return datetime.fromisoformat(iso).astimezone().strftime("%H:%M")


def _request_out(raw: dict) -> dict:
    return {
        "request_id": raw["request_id"],
        "status": raw["status"],
        "amount": _dollars(raw["amount_cents"]),
        "category": raw["category"],
        "merchant": raw["merchant"],
        "remaining": _dollars(raw["envelope_remaining_cents"]),
        "over_by": _dollars(raw["over_by_cents"]) or None,
        "created_at": raw["created_at"],
        "decided_at": raw["decided_at"],
        "expires_at": raw["expires_at"],
    }


@mcp.tool
async def check_budget(category: str | None = None, amount: float | None = None) -> dict:
    """Check the user's Wallie spending allowance (test dollars). Returns each budget category with its weekly limit, spent in the last 7 days, remaining, and the auto-approve threshold. Pass an amount (and category) to preview whether a purchase would be auto-approved, need the user's text approval, or be over budget. Use the exact category names returned here when calling request_spend."""
    try:
        raw = await db.check_budget(connector_key())
    except db.RpcError as exc:
        return {"status": "error", "message": exc.message}

    categories = [
        {
            "category": e["category"],
            "weekly_limit": _dollars(e["weekly_limit_cents"]),
            "spent": _dollars(e["spent_cents"]),
            "remaining": _dollars(e["remaining_cents"]),
            "auto_approve_under": _dollars(e["auto_approve_under_cents"]),
        }
        for e in (raw["envelopes"] or [])
    ]

    out = {
        "categories": categories,
        "weekly_budget": _dollars(raw["weekly_allowance_cents"]),
        "weekly_remaining": _dollars(raw["total_remaining_cents"]),
        "has_pending_request": raw["has_pending"],
    }

    if amount is not None and category is not None:
        match = next((c for c in categories if c["category"] == category.strip().lower()), None)
        if match is None:
            out["preview"] = {
                "outcome": "error",
                "message": f"Unknown category. Use one of: {', '.join(CATEGORIES)}.",
            }
        elif amount > match["remaining"]:
            out["preview"] = {
                "outcome": "over_budget",
                "over_by": round(amount - match["remaining"], 2),
            }
        elif amount < match["auto_approve_under"]:
            out["preview"] = {"outcome": "auto_approve"}
        else:
            out["preview"] = {"outcome": "needs_approval"}

    return out


@mcp.tool
async def request_spend(
    amount: float,
    category: str,
    merchant: str,
    description: str,
    url: str | None = None,
) -> dict:
    """Always call request_spend before any purchase. Asks Wallie, the user's spending allowance, for permission to spend. Never complete a purchase yourself: once approved, give the user the purchase link and let them buy it. Results: "approved" -> confirm and share the link. "pending" -> tell the user Wallie has texted them for approval, then call get_status with the request_id until it is no longer pending. "busy" -> another purchase is already waiting for approval; tell the user and do not retry until it resolves. "declined" or "expired" -> do not buy; tell the user. "error" -> read the message (for example, use one of the listed categories)."""
    if amount is None or amount <= 0:
        return {"status": "error", "message": "Amount must be greater than zero."}

    key = connector_key()
    try:
        raw = await db.request_spend(
            key, _cents(amount), category.strip().lower(), merchant, description
        )
    except db.RpcError as exc:
        return {"status": "error", "message": exc.message}

    if raw.get("decision") == "blocked":
        return await _blocked_response(key, raw["pending_request_id"], amount, merchant)

    out = _request_out(raw)
    if raw["decision"] == "approved":
        out["status"] = "approved"
        out["message"] = (
            f"Approved automatically ({out['amount']:.2f} is under the "
            f"{out['category']} auto-approve limit). Share the link with the user; "
            "they complete the purchase themselves."
        )
        return out

    sent = await notify.send_approval_text(
        {
            "request_id": raw["request_id"],
            "phone": raw["phone"],
            "kind": "over_budget" if raw["over_by_cents"] else "approval",
            "amount": _dollars(raw["amount_cents"]),
            "category": raw["category"],
            "merchant": raw["merchant"],
            "description": raw["description"],
            "url": url,
            "remaining": _dollars(raw["envelope_remaining_cents"]),
            "over_by": _dollars(raw["over_by_cents"]) or None,
        }
    )

    if not sent:
        return {
            "status": "error",
            "request_id": raw["request_id"],
            "message": "Wallie could not send the approval text. Tell the user, then try request_spend again.",
        }

    out["status"] = "pending"
    over = f" It is over budget by ${out['over_by']:.2f}." if out["over_by"] else ""
    out["message"] = (
        f"Wallie texted the user to approve ${out['amount']:.2f} at {out['merchant']}.{over} "
        f"Tell the user to reply Y or N, then call get_status with request_id "
        f"{out['request_id']} until it is no longer pending. It expires at "
        f"{_clock(out['expires_at'])}."
    )
    return out


async def _blocked_response(key: str, pending_id: str, amount: float, merchant: str) -> dict:
    """D8: an identical retry gets the existing request back; anything else is busy."""
    try:
        pending = await db.get_status(key, pending_id)
    except db.RpcError as exc:
        return {"status": "error", "message": exc.message}

    same = (
        pending["amount_cents"] == _cents(amount)
        and (pending["merchant"] or "").strip().lower() == merchant.strip().lower()
    )
    out = _request_out(pending)
    if same:
        out["status"] = "pending"
        out["message"] = (
            f"This request is already waiting for the user's approval (sent "
            f"{_clock(pending['created_at'])}, expires {_clock(pending['expires_at'])}). "
            f"Call get_status with request_id {out['request_id']}; do not create a new request."
        )
        return out

    out["status"] = "busy"
    out["message"] = (
        f"Another purchase is waiting for the user's approval: ${out['amount']:.2f} at "
        f"{out['merchant']} ({out['category']}), sent {_clock(pending['created_at'])}. "
        "Wallie allows one purchase at a time. Tell the user and do not retry until it is "
        f"approved, declined, or expires at {_clock(pending['expires_at'])}."
    )
    return out


@mcp.tool
async def get_status(request_id: str | None = None) -> dict:
    """Check a Wallie spend request. Waits up to about 25 seconds for the user's text reply before returning. If still "pending", tell the user you're still waiting for their reply, then call get_status again. "approved" -> confirm and share the purchase link. "declined" or "expired" -> do not buy and tell the user."""
    key = connector_key()

    if request_id is None:
        try:
            request_id = await db.latest_request_id(key)
        except db.RpcError as exc:
            return {"status": "error", "message": exc.message}
        if request_id is None:
            return {
                "status": "error",
                "message": "Wallie has no spend requests for this user yet. Call request_spend first.",
            }

    deadline = asyncio.get_running_loop().time() + POLL_TIMEOUT
    while True:
        try:
            raw = await db.get_status(key, request_id)
        except db.RpcError as exc:
            return {"status": "error", "message": exc.message}

        if raw["status"] != "pending" or asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(POLL_SECONDS)

    out = _request_out(raw)
    out["message"] = {
        "approved": "Approved. Share the purchase link with the user; they complete the purchase themselves.",
        "declined": "The user declined. Do not buy it; tell the user.",
        "expired": "The request expired with no reply. Do not buy it; tell the user they can ask again.",
        "pending": "Still waiting for the user's text reply. Tell the user you're still waiting, then call get_status again.",
    }[out["status"]]
    return out


class KeyFromPath:
    """ASGI middleware: rewrites /mcp/<key>[/] -> /mcp, moves <key> into a header."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            parts = [p for p in scope["path"].split("/") if p]
            if len(parts) == 2 and parts[0] == "mcp":
                headers = [(k, v) for k, v in scope["headers"] if k != b"x-wallie-key"]
                headers.append((b"x-wallie-key", parts[1].encode()))
                scope = {**scope, "path": "/mcp", "raw_path": b"/mcp", "headers": headers}
        await self.app(scope, receive, send)


app = KeyFromPath(mcp.http_app(path="/mcp", stateless_http=True))
# run: uv run uvicorn server:app --port 8000
