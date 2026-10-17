import asyncio
from datetime import datetime

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request

import db
import notify

load_dotenv()

mcp = FastMCP("Wallie")

CATEGORIES = "food, travel, shopping, software"
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
    """The shared shape of a request, in dollars."""
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
    except db.DbError as exc:
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
        "weekly_spent": _dollars(raw["total_spent_cents"]),
        "weekly_remaining": _dollars(raw["total_remaining_cents"]),
        "has_pending_request": raw["has_pending"],
    }

    if amount is not None and category is not None:
        wanted = category.strip().lower()
        match = next((c for c in categories if c["category"].strip().lower() == wanted), None)
        if match is None:
            out["preview"] = {
                "outcome": "error",
                "message": f"No budget category called '{category}'. Use one of: {CATEGORIES}.",
            }
        elif amount > min(match["remaining"], out["weekly_remaining"]):
            short = min(match["remaining"], out["weekly_remaining"])
            out["preview"] = {"outcome": "over_budget", "over_by": round(amount - short, 2)}
        elif amount < match["auto_approve_under"]:
            out["preview"] = {"outcome": "auto_approve"}
        else:
            out["preview"] = {"outcome": "needs_approval"}

    if out["has_pending_request"]:
        out["note"] = (
            "A purchase is already waiting for the user's approval, so a new request_spend "
            "would return busy until it resolves."
        )

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
    except db.DbError as exc:
        return {"status": "error", "message": exc.message}

    if raw.get("decision") == "blocked":
        return await _blocked(key, raw["pending_request_id"], amount, merchant)

    out = _request_out(raw)

    if raw["decision"] == "approved":
        out["status"] = "approved"
        out["message"] = (
            f"Approved automatically: ${out['amount']:.2f} is under the {out['category']} "
            f"auto-approve limit. ${out['remaining']:.2f} left in {out['category']} this week. "
            "Share the link with the user; they complete the purchase themselves."
        )
        return out

    # decision == 'pending': either over the auto-approve amount or over budget.
    sent = await notify.send_approval_text(
        {
            "request_id": out["request_id"],
            "phone": raw["phone"],
            "kind": "over_budget" if out["over_by"] else "approval",
            "amount": out["amount"],
            "category": out["category"],
            "merchant": out["merchant"],
            "description": raw.get("description"),
            "url": url,
            "remaining": out["remaining"],
            "over_by": out["over_by"],
        }
    )

    if not sent:
        # D4: nobody can approve a text that never arrived. Release the lock.
        await db.expire_request(out["request_id"])
        return {
            "status": "error",
            "message": "Wallie could not send the approval text, so the request was cancelled. "
            "Tell the user, then try request_spend again.",
        }

    out["status"] = "pending"
    if out["over_by"]:
        detail = f"It is over the {out['category']} budget by ${out['over_by']:.2f}. "
    else:
        detail = f"${out['remaining']:.2f} left in {out['category']} this week. "
    out["message"] = (
        f"Wallie texted the user to approve ${out['amount']:.2f} at {out['merchant']}. {detail}"
        f"Tell the user to reply Y or N, then call get_status with request_id "
        f"{out['request_id']} until it is no longer pending. It expires at "
        f"{_clock(out['expires_at'])}."
    )
    return out


async def _blocked(key: str, pending_id: str, amount: float, merchant: str) -> dict:
    """D8: an identical retry gets the existing request back; anything else is busy.

    The RPC only reports that something is pending, so the comparison happens here.
    """
    try:
        pending = await db.get_status(key, pending_id)
    except db.DbError as exc:
        return {"status": "error", "message": exc.message}

    out = _request_out(pending)
    same = pending["amount_cents"] == _cents(amount) and (
        (pending["merchant"] or "").strip().lower() == merchant.strip().lower()
    )

    if same:
        out["status"] = "pending"
        out["message"] = (
            f"This exact request is already waiting for the user's approval (sent "
            f"{_clock(out['created_at'])}, expires {_clock(out['expires_at'])}). Call get_status "
            f"with request_id {out['request_id']}; do not create another request."
        )
        return out

    out["status"] = "busy"
    out["message"] = (
        f"Another purchase is waiting for the user's approval: ${out['amount']:.2f} at "
        f"{out['merchant']} ({out['category']}), sent {_clock(out['created_at'])}. "
        "Wallie allows one purchase at a time. Tell the user and do not retry until it is "
        f"approved, declined, or expires at {_clock(out['expires_at'])}."
    )
    return out


@mcp.tool
async def get_status(request_id: str | None = None) -> dict:
    """Check a Wallie spend request. Waits up to about 25 seconds for the user's text reply before returning. If still "pending", tell the user you're still waiting for their reply, then call get_status again. "approved" -> confirm and share the purchase link. "declined" or "expired" -> do not buy and tell the user."""
    key = connector_key()

    if request_id is None:
        try:
            request_id = await db.latest_request_id(key)
        except db.DbError as exc:
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
        except db.DbError as exc:
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


@mcp.custom_route("/health", methods=["GET", "OPTIONS"])
async def health(request):
    """Liveness check for the website's Connect screen.

    CORS-open and unauthenticated on purpose: it reveals nothing about any user, and the
    browser needs it cross-origin to tell "the server moved" apart from "the link is wrong".
    """
    from starlette.responses import JSONResponse

    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Cache-Control": "no-store",
    }
    if request.method == "OPTIONS":
        from starlette.responses import Response

        return Response(status_code=204, headers=headers)

    ok = True
    detail = "ready"
    try:
        await db.check_budget("health-check-not-a-real-key")
    except db.DbError as exc:
        # An invalid key proves the database answered. Anything else means it did not.
        if exc.code != "invalid_connector_key":
            ok, detail = False, exc.message
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"database unreachable: {type(exc).__name__}"

    return JSONResponse(
        {"ok": ok, "service": "wallie-mcp", "database": detail},
        status_code=200 if ok else 503,
        headers=headers,
    )


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
