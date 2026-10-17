"""Wallie SMS service: sends approval texts and applies Y/N replies.

Two endpoints:

  POST /notify      the MCP server calls this when a purchase needs approval.
                    Verifies x-wallie-secret, then sends the SMS via Twilio.
  POST /sms-reply   Twilio calls this when the user texts back. Hands the reply to
                    the database's resolve_reply() and answers with TwiML.

The decision logic is NOT here on purpose: resolve_reply() in the database already
finds the user by phone, applies the 10-minute expiry, updates the pending request and
reports what happened. This service only moves messages.

Run:  uv run uvicorn main:app --port 9000
"""

import os

import httpx
import psycopg
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
NOTIFY_SECRET = os.environ.get("NOTIFY_SECRET", "").strip()

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_FROM = os.environ.get("TWILIO_FROM", "").strip()

TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


def approval_text(p: dict) -> str:
    amount = f"${p['amount']:.2f}"
    if p.get("kind") == "over_budget":
        return (
            f"Wallie: Claude wants {amount} at {p['merchant']} ({p['category']}). "
            f"Over by ${p['over_by']:.2f}. Approve anyway? Y/N"
        )
    return (
        f"Wallie: Claude wants to spend {amount} at {p['merchant']} ({p['category']}). "
        f"${p['remaining']:.2f} left this week. Reply Y or N."
    )


async def send_sms(to: str, body: str) -> None:
    """Sends via Twilio, or prints if Twilio is not configured yet (dry run)."""
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM):
        print(f"[dry-run] would text {to}: {body}", flush=True)
        return

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            TWILIO_API.format(sid=TWILIO_ACCOUNT_SID),
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            data={"To": to, "From": TWILIO_FROM, "Body": body},
        )
    # Raise so /notify returns non-2xx and the MCP server tells Claude to retry.
    resp.raise_for_status()


async def notify(request):
    if NOTIFY_SECRET and request.headers.get("x-wallie-secret") != NOTIFY_SECRET:
        return JSONResponse({"error": "bad secret"}, status_code=401)

    payload = await request.json()
    try:
        await send_sms(payload["phone"], approval_text(payload))
    except Exception as exc:  # noqa: BLE001 - the MCP server just needs a non-2xx
        print(f"[notify] send failed: {exc}", flush=True)
        return JSONResponse({"error": "send failed"}, status_code=502)

    return JSONResponse({"ok": True})


def twiml(message: str) -> Response:
    body = f"<?xml version='1.0' encoding='UTF-8'?><Response><Message>{message}</Message></Response>"
    return Response(body, media_type="application/xml")


REPLIES = {
    "approved": "Approved. Wallie will share the purchase link with Claude.",
    "declined": "Declined. Wallie told Claude not to buy it.",
    "expired": "That request expired.",
    "no_pending": "Nothing is waiting for approval.",
    "invalid_reply": "Please reply Y to approve or N to decline.",
    "unknown_phone": "This number is not set up with Wallie.",
}


async def sms_reply(request):
    form = await request.form()
    phone = (form.get("From") or "").strip()
    body = (form.get("Body") or "").strip()

    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        async with conn.cursor() as cur:
            await cur.execute("select public.resolve_reply(%s, %s)", (phone, body))
            result = (await cur.fetchone())[0]

    print(f"[sms-reply] {phone} said {body!r} -> {result.get('result')}", flush=True)
    return twiml(REPLIES.get(result.get("result"), "Sorry, something went wrong."))


async def health(request):
    return JSONResponse(
        {
            "ok": True,
            "twilio_configured": bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM),
            "secret_set": bool(NOTIFY_SECRET),
            "database_set": bool(DATABASE_URL),
        }
    )


app = Starlette(
    routes=[
        Route("/notify", notify, methods=["POST"]),
        Route("/sms-reply", sms_reply, methods=["POST"]),
        Route("/", health, methods=["GET"]),
    ]
)
