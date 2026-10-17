"""Hands an approval text to the SMS person's service, or prints it in dev."""

import os

import httpx

TIMEOUT = 5.0


def _preview(payload: dict) -> str:
    amount = f"${payload['amount']:.2f}"
    left = f"${payload['remaining']:.2f}"
    if payload["kind"] == "over_budget":
        return (
            f"Wallie: Claude wants {amount} at {payload['merchant']} "
            f"({payload['category']}). Over by ${payload['over_by']:.2f}. Approve anyway? Y/N"
        )
    return (
        f"Wallie: Claude wants to spend {amount} at {payload['merchant']} "
        f"({payload['category']}). {left} left this week. Reply Y or N."
    )


async def send_approval_text(payload: dict) -> bool:
    """True if the text was accepted. In console mode always True."""
    url = os.environ.get("NOTIFY_URL", "").strip()
    if not url:
        print(f"[notify:console] to {payload['phone']}: {_preview(payload)}", flush=True)
        print(f"[notify:console] reply with: uv run python scripts/reply.py y", flush=True)
        return True

    headers = {"x-wallie-secret": os.environ.get("NOTIFY_SECRET", "")}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        print(f"[notify:error] {type(exc).__name__} calling {url}: {exc}", flush=True)
        return False

    if resp.is_success:
        return True

    # Say why. A silent failure here looks identical to a Twilio problem, an auth
    # problem and a crashed texting service, and the request gets cancelled either way.
    body = " ".join(resp.text.split())[:200] or "(empty body)"
    hint = {
        401: "the shared NOTIFY_SECRET does not match",
        403: "the texting service refused the request",
        404: "NOTIFY_URL is wrong, or its tunnel has moved",
        502: "the texting service is reachable but its handler failed (often Twilio "
        "credentials, or a trial account texting an unverified number)",
        503: "the texting service is down",
    }.get(resp.status_code, "unexpected status")
    print(f"[notify:error] POST {url} -> {resp.status_code} ({hint}): {body}", flush=True)
    return False
