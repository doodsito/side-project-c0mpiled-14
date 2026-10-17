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
        return resp.is_success
    except httpx.HTTPError as exc:
        print(f"[notify:error] {exc}", flush=True)
        return False
