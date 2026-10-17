import asyncio
from decimal import Decimal

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request

import db
import notify

load_dotenv()

mcp = FastMCP("Wallie")

POLL_SECONDS = 1
POLL_TIMEOUT = 25


def connector_key() -> str:
    return get_http_request().headers.get("x-wallie-key", "")


def _money(value) -> float:
    return float(round(Decimal(str(value or 0)), 2))


def _clock(value) -> str:
    return value.astimezone().strftime("%H:%M") if value else ""


def _request_out(row: dict) -> dict:
    return {
        "request_id": str(row["id"]),
        "status": row["status"],
        "amount": _money(row["amount"]),
        "category": row["category"],
        "merchant": row["merchant"],
        "url": row["url"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "decided_at": row["decided_at"].isoformat() if row["decided_at"] else None,
        "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
    }


@mcp.tool
async def check_budget(category: str | None = None, amount: float | None = None) -> dict:
    """Check the user's Wallie spending allowance (test dollars). Returns each budget category with its weekly limit, spent in the last 7 days, reserved by pending requests, remaining, and the auto-approve threshold. Pass an amount (and category) to preview whether a purchase would be auto-approved, need the user's text approval, or be over budget. Use the exact category names returned here when calling request_spend."""
    try:
        data = await db.budget(connector_key())
    except db.DbError as exc:
        return {"status": "error", "message": exc.message}

    categories = [
        {
            "category": e["category"],
            "weekly_limit": _money(e["weekly_limit"]),
            "spent": _money(e["spent"]),
            "reserved": _money(e["reserved"]),
            "remaining": _money(e["weekly_limit"] - e["spent"] - e["reserved"]),
            "auto_approve_under": _money(e["auto_approve_under"]),
        }
        for e in data["envelopes"]
    ]

    out = {
        "categories": categories,
        "weekly_budget": _money(data["user"]["weekly_budget"]),
        "pending_request": _request_out(data["pending"]) if data["pending"] else None,
    }

    if amount is not None and category is not None:
        wanted = category.strip().lower()
        match = next((c for c in categories if c["category"].strip().lower() == wanted), None)
        if match is None:
            names = ", ".join(c["category"] for c in categories) or "none yet"
            out["preview"] = {
                "outcome": "error",
                "message": f"No budget category called '{category}'. Use one of: {names}.",
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

    money = Decimal(str(amount)).quantize(Decimal("0.01"))
    try:
        result = await db.create_request(
            connector_key(), money, category, merchant, description, url
        )
    except db.DbError as exc:
        return {"status": "error", "message": exc.message}

    row = result["request"]
    out = _request_out(row)

    if result["outcome"] == "busy":
        out["status"] = "busy"
        out["message"] = (
            f"Another purchase is waiting for the user's approval: ${out['amount']:.2f} at "
            f"{out['merchant']} ({out['category']}), sent {_clock(row['created_at'])}. "
            "Wallie allows one purchase at a time. Tell the user and do not retry until it is "
            f"approved, declined, or expires at {_clock(row['expires_at'])}."
        )
        return out

    if result["outcome"] == "idempotent":
        out["status"] = "pending"
        out["message"] = (
            f"This exact request is already waiting for the user's approval (sent "
            f"{_clock(row['created_at'])}, expires {_clock(row['expires_at'])}). Call get_status "
            f"with request_id {out['request_id']}; do not create another request."
        )
        return out

    if result["outcome"] == "auto_approve":
        out["status"] = "approved"
        out["remaining"] = _money(result["remaining"] - row["amount"])
        out["message"] = (
            f"Approved automatically: ${out['amount']:.2f} is under the {out['category']} "
            f"auto-approve limit. ${out['remaining']:.2f} left in {out['category']} this week. "
            "Share the link with the user; they complete the purchase themselves."
        )
        return out

    # pending: over_budget or needs_approval
    over_by = result["over_by"]
    sent = await notify.send_approval_text(
        {
            "request_id": out["request_id"],
            "phone": result["user"]["phone"],
            "kind": "over_budget" if over_by else "approval",
            "amount": out["amount"],
            "category": out["category"],
            "merchant": out["merchant"],
            "description": row["description"],
            "url": out["url"],
            "remaining": _money(result["remaining"]),
            "over_by": _money(over_by) if over_by else None,
        }
    )

    if not sent:
        # D4: the text failed, so nobody can approve it. Release the lock.
        await db.expire_request(out["request_id"])
        return {
            "status": "error",
            "message": "Wallie could not send the approval text, so the request was cancelled. "
            "Tell the user, then try request_spend again.",
        }

    out["status"] = "pending"
    out["remaining"] = _money(result["remaining"])
    if over_by:
        out["over_by"] = _money(over_by)
        detail = f"It is over the {out['category']} budget by ${out['over_by']:.2f}. "
    else:
        detail = f"${out['remaining']:.2f} left in {out['category']} this week. "
    out["message"] = (
        f"Wallie texted the user to approve ${out['amount']:.2f} at {out['merchant']}. {detail}"
        f"Tell the user to reply Y or N, then call get_status with request_id "
        f"{out['request_id']} until it is no longer pending. It expires at "
        f"{_clock(row['expires_at'])}."
    )
    return out


@mcp.tool
async def get_status(request_id: str | None = None) -> dict:
    """Check a Wallie spend request. Waits up to about 25 seconds for the user's text reply before returning. If still "pending", tell the user you're still waiting for their reply, then call get_status again. "approved" -> confirm and share the purchase link. "declined" or "expired" -> do not buy and tell the user."""
    key = connector_key()
    deadline = asyncio.get_running_loop().time() + POLL_TIMEOUT

    while True:
        try:
            row = await db.read_request(key, request_id)
        except db.DbError as exc:
            return {"status": "error", "message": exc.message}

        if row is None:
            return {
                "status": "error",
                "message": "Wallie has no such spend request. Call request_spend first."
                if request_id
                else "Wallie has no spend requests for this user yet. Call request_spend first.",
            }

        if row["status"] != "pending" or asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(POLL_SECONDS)

    out = _request_out(row)
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
