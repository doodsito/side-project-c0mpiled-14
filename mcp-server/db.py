"""Postgres access for the Wallie MCP server.

Schema: supabase/migrations/20261017211932_align_with_team_contract.sql
(users / envelopes / requests, numeric dollars, free-text categories).

The approval rules live in rules.py; the atomic check-and-insert lives in
create_request() below, which holds a row lock on the user for its whole
transaction (PLAN.md D6/D8).
"""

import os

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from rules import decide

EXPIRY_MINUTES = 10

_pool: AsyncConnectionPool | None = None


class DbError(Exception):
    """Carries a message written for Claude to read aloud."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


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


async def _conn():
    p = pool()
    await p.open()
    return p.connection()


EXPIRE_STALE = f"""
    update requests set status = 'expired', decided_at = now()
    where user_id = %s and status = 'pending'
      and created_at < now() - interval '{EXPIRY_MINUTES} minutes'
"""

ENVELOPE_STATUS = """
    select e.id, e.category, e.weekly_limit, e.auto_approve_under,
      coalesce(sum(r.amount) filter (
        where r.status = 'approved' and r.created_at > now() - interval '7 days'), 0) as spent,
      coalesce(sum(r.amount) filter (where r.status = 'pending'), 0) as reserved
    from envelopes e
    left join requests r on r.envelope_id = e.id
    where e.user_id = %s
    group by e.id
    order by e.category
"""

REQUEST_FIELDS = """
    r.id, r.amount, r.merchant, r.description, r.url, r.status,
    r.created_at, r.decided_at,
    r.created_at + interval '%s minutes' as expires_at,
    e.category
""" % EXPIRY_MINUTES


async def get_user(key: str) -> dict:
    async with await _conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select id, phone, weekly_budget from users where connector_key = %s", (key,)
            )
            user = await cur.fetchone()
    if user is None:
        raise DbError(
            "This Wallie connector link is not valid. Ask the user to re-copy it from "
            "their Wallie setup page."
        )
    return user


async def budget(key: str) -> dict:
    """Per-envelope status plus the user's pending request, if any."""
    user = await get_user(key)
    async with await _conn() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(EXPIRE_STALE, (user["id"],))
                await cur.execute(ENVELOPE_STATUS, (user["id"],))
                envelopes = await cur.fetchall()
                await cur.execute(
                    f"""select {REQUEST_FIELDS} from requests r
                        join envelopes e on e.id = r.envelope_id
                        where r.user_id = %s and r.status = 'pending'""",
                    (user["id"],),
                )
                pending = await cur.fetchone()
    return {"user": user, "envelopes": envelopes, "pending": pending}


async def create_request(
    key: str,
    amount,
    category: str,
    merchant: str,
    description: str | None,
    url: str | None,
) -> dict:
    """Atomic check-and-insert. One transaction, user row locked throughout (D8)."""
    async with await _conn() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                # 2. Lock the user.
                await cur.execute(
                    "select id, phone, weekly_budget from users where connector_key = %s for update",
                    (key,),
                )
                user = await cur.fetchone()
                if user is None:
                    raise DbError(
                        "This Wallie connector link is not valid. Ask the user to re-copy it "
                        "from their Wallie setup page."
                    )

                # 3. Expire stale pending requests.
                await cur.execute(EXPIRE_STALE, (user["id"],))

                # 4. A pending request is the lock.
                await cur.execute(
                    f"""select {REQUEST_FIELDS} from requests r
                        join envelopes e on e.id = r.envelope_id
                        where r.user_id = %s and r.status = 'pending'""",
                    (user["id"],),
                )
                pending = await cur.fetchone()
                if pending is not None:
                    same = pending["amount"] == amount and (
                        (pending["merchant"] or "").strip().lower() == merchant.strip().lower()
                    )
                    return {"outcome": "idempotent" if same else "busy", "request": pending}

                # 5. Match the category (case-insensitive, trimmed).
                await cur.execute(ENVELOPE_STATUS, (user["id"],))
                envelopes = await cur.fetchall()
                if not envelopes:
                    raise DbError(
                        "This user has no budget categories yet. Tell them to finish Wallie setup."
                    )
                wanted = category.strip().lower()
                envelope = next(
                    (e for e in envelopes if e["category"].strip().lower() == wanted), None
                )
                if envelope is None:
                    names = ", ".join(e["category"] for e in envelopes)
                    raise DbError(f"No budget category called '{category}'. Use one of: {names}.")

                # 6. Decide.
                d = decide(
                    amount=amount,
                    weekly_limit=envelope["weekly_limit"],
                    spent=envelope["spent"],
                    reserved=envelope["reserved"],
                    auto_approve_under=envelope["auto_approve_under"],
                )
                status = "approved" if d.outcome == "auto_approve" else "pending"

                # 7. Insert.
                try:
                    await cur.execute(
                        f"""
                        insert into requests
                          (user_id, envelope_id, amount, merchant, description, url, status, decided_at)
                        values (%s, %s, %s, %s, %s, %s, %s, case when %s = 'approved' then now() end)
                        returning id
                        """,
                        (
                            user["id"],
                            envelope["id"],
                            amount,
                            merchant,
                            description,
                            url,
                            status,
                            status,
                        ),
                    )
                    new_id = (await cur.fetchone())["id"]
                except psycopg.errors.UniqueViolation:
                    # The partial unique index caught a race the row lock somehow let through.
                    return {"outcome": "busy", "request": pending}

                await cur.execute(
                    f"""select {REQUEST_FIELDS} from requests r
                        join envelopes e on e.id = r.envelope_id where r.id = %s""",
                    (new_id,),
                )
                request = await cur.fetchone()
        # 8. Committed here.

    remaining = envelope["weekly_limit"] - envelope["spent"] - envelope["reserved"]
    return {
        "outcome": d.outcome,
        "request": request,
        "user": user,
        "remaining": remaining,
        "over_by": d.over_by,
    }


async def read_request(key: str, request_id: str | None) -> dict | None:
    """One request by id, or the user's latest. Applies lazy expiry first (D5/D9)."""
    user = await get_user(key)
    async with await _conn() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(EXPIRE_STALE, (user["id"],))
                if request_id is None:
                    await cur.execute(
                        f"""select {REQUEST_FIELDS} from requests r
                            join envelopes e on e.id = r.envelope_id
                            where r.user_id = %s
                            order by r.created_at desc limit 1""",
                        (user["id"],),
                    )
                else:
                    await cur.execute(
                        f"""select {REQUEST_FIELDS} from requests r
                            join envelopes e on e.id = r.envelope_id
                            where r.user_id = %s and r.id = %s""",
                        (user["id"], request_id),
                    )
                return await cur.fetchone()


async def envelope_remaining(request_id: str):
    """Remaining in the envelope a request belongs to (for the approval text)."""
    async with await _conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select e.weekly_limit
                  - coalesce(sum(r2.amount) filter (
                      where r2.status = 'approved' and r2.created_at > now() - interval '7 days'), 0)
                  - coalesce(sum(r2.amount) filter (where r2.status = 'pending'), 0) as remaining
                from requests r
                join envelopes e on e.id = r.envelope_id
                left join requests r2 on r2.envelope_id = e.id
                where r.id = %s
                group by e.id, e.weekly_limit
                """,
                (request_id,),
            )
            row = await cur.fetchone()
    return row["remaining"] if row else 0


async def phone_awaiting_reply() -> str | None:
    """The phone of the only user with a pending request, if there is exactly one.

    Lets scripts/reply.py work without configuring DEMO_PHONE.
    """
    async with await _conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """select u.phone from users u
                   join requests r on r.user_id = u.id and r.status = 'pending'
                   limit 2"""
            )
            rows = await cur.fetchall()
    return rows[0]["phone"] if len(rows) == 1 else None


async def expire_request(request_id: str) -> None:
    """Release the lock when the approval text could not be sent (D4)."""
    async with await _conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """update requests set status = 'expired', decided_at = now()
                   where id = %s and status = 'pending'""",
                (request_id,),
            )


async def resolve_reply(phone: str, reply: str) -> dict:
    """Person 3 owns this in production; scripts/reply.py uses it in dev."""
    yes = reply.strip().lower() in ("y", "yes")
    status = "approved" if yes else "declined"
    async with await _conn() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute("select id from users where phone = %s for update", (phone,))
                user = await cur.fetchone()
                if user is None:
                    return {"result": "unknown_phone"}
                await cur.execute(EXPIRE_STALE, (user["id"],))
                await cur.execute(
                    """update requests set status = %s, decided_at = now()
                       where user_id = %s and status = 'pending'
                       returning id, amount, merchant, status""",
                    (status, user["id"]),
                )
                row = await cur.fetchone()
    if row is None:
        return {"result": "no_pending"}
    return {"result": status, **row}


async def delete_requests(key: str) -> int:
    async with await _conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """delete from requests
                   where user_id = (select id from users where connector_key = %s)""",
                (key,),
            )
            return cur.rowcount
