"""Adapter over the Postgres RPCs owned by the database person.

Schema + contract: supabase/migrations/20261017214205_restore_rpc_schema.sql and
supabase/README.md. The budget math, the approval decision and the
one-pending-per-user lock all live in SQL; this module converts at the boundary
and maps raised exceptions to messages Claude can read aloud.

Money is integer cents in the database and in everything here. server.py converts
to dollars for Claude.
"""

import os

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

_pool: AsyncConnectionPool | None = None

# Raised by the RPCs (see supabase/README.md).
KNOWN_ERRORS = {
    "invalid_connector_key": (
        "This Wallie connector link is not valid. Ask the user to re-copy it from their "
        "Wallie setup page."
    ),
    "invalid_category": "Unknown budget category. Use one of: food, travel, shopping, software.",
    "request_not_found": "Wallie has no such spend request.",
    "not_authenticated": "Wallie could not identify the user.",
}


class DbError(Exception):
    """Carries a message written for Claude to read aloud."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.message = message
        self.code = code


def pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        conninfo = os.environ.get("DATABASE_URL", "").strip()
        if not conninfo:
            raise DbError(
                "Wallie is not connected to its database yet (DATABASE_URL is unset). "
                "Tell the user Wallie is still being set up."
            )
        # Supabase's transaction pooler (port 6543) cannot use prepared statements.
        kwargs = {"prepare_threshold": None} if ":6543" in conninfo else {}
        _pool = AsyncConnectionPool(
            conninfo,
            min_size=1,
            max_size=4,
            open=False,
            kwargs={"row_factory": dict_row, **kwargs},
        )
    return _pool


async def _call(sql: str, params: tuple = ()):
    p = pool()
    await p.open()
    try:
        async with p.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
                if row is None:
                    return None
                return next(iter(row.values()))
    except psycopg.errors.DatabaseError as exc:
        raw = (getattr(exc.diag, "message_primary", "") or "").strip()
        if raw in KNOWN_ERRORS:
            raise DbError(KNOWN_ERRORS[raw], raw) from exc
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
    """the SMS person owns this in production; scripts/reply.py uses it in dev."""
    return await _call("select public.resolve_reply(%s, %s)", (phone, reply))


# --- Reads that have no RPC. service_role has table access (see the migration's grants). ---


async def latest_request_id(key: str) -> str | None:
    """The user's most recent request, for get_status's optional request_id (D9)."""
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


async def phone_awaiting_reply() -> str | None:
    """The phone of the only user with a pending request, if there is exactly one.

    Lets scripts/reply.py work without configuring DEMO_PHONE.
    """
    p = pool()
    await p.open()
    async with p.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """select p.phone from public.profiles p
                   join public.spend_requests r on r.user_id = p.id and r.status = 'pending'
                   limit 2"""
            )
            rows = await cur.fetchall()
    return rows[0]["phone"] if len(rows) == 1 else None


async def expire_request(request_id: str) -> None:
    """Release the lock when the approval text could not be sent (D4)."""
    p = pool()
    await p.open()
    async with p.connection() as conn:
        await conn.execute(
            """update public.spend_requests
               set status = 'expired', decided_by = 'timeout', decided_at = now()
               where id = %s and status = 'pending'""",
            (request_id,),
        )


async def delete_all_requests() -> int:
    """Every spend request, for resetting a demo. Test dollars only."""
    p = pool()
    await p.open()
    async with p.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("delete from public.spend_requests")
            return cur.rowcount


async def delete_requests_by_phone(phone: str) -> int:
    p = pool()
    await p.open()
    async with p.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """delete from public.spend_requests
                   where user_id = (
                     select id from public.profiles where phone = %s
                   )""",
                ("+" + phone.strip().lstrip("+"),),
            )
            return cur.rowcount


async def delete_requests(key: str) -> int:
    p = pool()
    await p.open()
    async with p.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """delete from public.spend_requests
                   where user_id = (
                     select id from public.profiles where connector_key_hash = private.hash_key(%s)
                   )""",
                (key,),
            )
            return cur.rowcount
