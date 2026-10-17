"""Adapter over Person 2's Postgres RPCs (see supabase/README.md)."""

import os

import psycopg
from psycopg_pool import AsyncConnectionPool

_pool: AsyncConnectionPool | None = None

# Raised by the RPCs; mapped to our "error" status.
KNOWN_ERRORS = {
    "invalid_connector_key": "This Wallie connector link is not valid. Ask the user to re-copy it from their Wallie setup page.",
    "invalid_category": "Unknown category. Use one of: food, travel, shopping, software.",
    "request_not_found": "That spend request does not exist.",
}


class RpcError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        conninfo = os.environ.get("DATABASE_URL", "").strip()
        if not conninfo:
            raise RpcError(
                "no_database",
                "Wallie is not connected to its database yet (DATABASE_URL is unset). Tell the user Wallie is still being set up.",
            )
        # Supabase's transaction pooler (port 6543) cannot use prepared statements.
        kwargs = {"prepare_threshold": None} if ":6543" in conninfo else {}
        _pool = AsyncConnectionPool(conninfo, min_size=1, max_size=4, open=False, kwargs=kwargs)
    return _pool


async def _call(sql: str, params: tuple):
    p = pool()
    await p.open()
    try:
        async with p.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
                return row[0] if row else None
    except psycopg.errors.DatabaseError as exc:
        code = (getattr(exc.diag, "message_primary", "") or str(exc)).strip()
        if code in KNOWN_ERRORS:
            raise RpcError(code, KNOWN_ERRORS[code]) from exc
        raise


async def check_budget(key: str) -> dict:
    return await _call("select public.check_budget(%s)", (key,))


async def request_spend(
    key: str, amount_cents: int, category: str, merchant: str, description: str | None
) -> dict:
    return await _call(
        "select public.request_spend(%s, %s, %s, %s, %s)",
        (key, amount_cents, category, merchant, description),
    )


async def get_status(key: str, request_id: str) -> dict:
    return await _call("select public.get_status(%s, %s)", (key, request_id))


async def resolve_reply(phone: str, reply: str) -> dict:
    """Person 3 owns this in production; used by scripts/reply.py in dev."""
    return await _call("select public.resolve_reply(%s, %s)", (phone, reply))


async def latest_request_id(key: str) -> str | None:
    """The user's most recent request, pending or not (D9's default for get_status)."""
    return await _call(
        """
        select r.id::text
        from public.spend_requests r
        join public.profiles p on p.id = r.user_id
        where p.connector_key_hash = private.hash_key(%s)
        order by r.created_at desc
        limit 1
        """,
        (key,),
    )
