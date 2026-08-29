"""Leads, and the events that hang off them.

`value_cents` never appears in an INSERT here. It is owner-entered, it is the
number every report is built from, and nothing on the call path is allowed to
write it. The only writer is `set_value`, which the SMS command grammar and the
portal call with a figure a human typed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True, slots=True)
class LeadRow:
    id: UUID
    caller_name: str | None
    job_type: str | None
    urgency: str
    status: str
    created_at: datetime
    value_cents: int | None = None


async def create(
    conn: AsyncConnection,
    *,
    tenant_id: UUID,
    contact_id: UUID | None,
    call_id: UUID | None,
    caller_name: str | None,
    callback_e164: str | None,
    service_address: str | None,
    job_type: str | None,
    description: str | None,
    urgency: str,
    source: str | None,
    escalated_at: datetime | None = None,
) -> UUID:
    """Write the lead. Note what is absent: no value, no price, no quantity.

    Everything here came from the caller through Mabel, so none of it is money.
    """
    result = await conn.execute(
        text(
            """
            INSERT INTO leads (tenant_id, contact_id, call_id, caller_name, callback_e164,
                               service_address, job_type, description, urgency, source,
                               escalated_at)
            VALUES (:tenant_id, :contact_id, :call_id, :caller_name, :callback_e164,
                    :service_address, :job_type, :description, :urgency, :source,
                    :escalated_at)
            RETURNING id
            """
        ),
        {
            "tenant_id": tenant_id,
            "contact_id": contact_id,
            "call_id": call_id,
            "caller_name": caller_name,
            "callback_e164": callback_e164,
            "service_address": service_address,
            "job_type": job_type,
            "description": description,
            "urgency": urgency,
            "source": source,
            "escalated_at": escalated_at,
        },
    )
    return result.scalar_one()


async def set_value(
    conn: AsyncConnection, lead_id: UUID, *, value_cents: int, currency: str = "USD"
) -> None:
    """The only writer of `value_cents`, and it takes an integer.

    Callers: the SMS command grammar (`WON RUIZ 3800`) and the portal's
    'What's this job worth?' field. Both are a human typing a number.
    """
    if isinstance(value_cents, bool) or not isinstance(value_cents, int):
        raise TypeError(f"value_cents must be integer cents, got {type(value_cents).__name__}")
    await conn.execute(
        text(
            "UPDATE leads SET value_cents = :value, currency = :currency, updated_at = now() "
            "WHERE id = :id"
        ),
        {"id": lead_id, "value": value_cents, "currency": currency},
    )


async def mark_touched(
    conn: AsyncConnection, lead_id: UUID, *, now: datetime | None = None
) -> None:
    """First contact. Clears the lead from the nudge sweep and the dashboard's
    'needs you' list. Only set once — the age shown is age until *first* touch."""
    await conn.execute(
        text(
            "UPDATE leads "
            "SET first_touched_at = "
            "  coalesce(first_touched_at, coalesce(cast(:now as timestamptz), now())), "
            "    updated_at = now() "
            "WHERE id = :id"
        ),
        {"id": lead_id, "now": now},
    )


async def set_status(
    conn: AsyncConnection,
    lead_id: UUID,
    status: str,
    *,
    lost_reason: str | None = None,
    now: datetime | None = None,
) -> None:
    """`won_at` is set here rather than by the caller, so a won lead always
    carries the timestamp the domain model requires."""
    await conn.execute(
        text(
            """
            UPDATE leads
            SET status = :status,
                lost_reason = CASE WHEN cast(:status as text) = 'lost'
                               THEN cast(:lost_reason as text) ELSE lost_reason END,
                won_at = CASE WHEN cast(:status as text) = 'won'
                              THEN coalesce(won_at, coalesce(cast(:now as timestamptz), now()))
                              ELSE won_at END,
                first_touched_at =
                  coalesce(first_touched_at, coalesce(cast(:now as timestamptz), now())),
                updated_at = now()
            WHERE id = :id
            """
        ),
        {"id": lead_id, "status": status, "lost_reason": lost_reason, "now": now},
    )


async def job_history(conn: AsyncConnection, contact_id: UUID, *, limit: int = 5) -> list[LeadRow]:
    """Past jobs for a caller we already know, so she can pick up the thread.

    `value_cents` comes back so the *portal* can show it. It is deliberately
    not passed through to the voice agent — see the tool handler.
    """
    result = await conn.execute(
        text(
            """
            SELECT id, caller_name, job_type, urgency, status, created_at, value_cents
            FROM leads
            WHERE contact_id = :contact_id
            ORDER BY created_at DESC
            LIMIT :limit
            """
        ),
        {"contact_id": contact_id, "limit": limit},
    )
    return [_to_row(row) for row in result.mappings()]


async def last_job_summary(conn: AsyncConnection, contact_id: UUID) -> dict[str, Any] | None:
    """What `lookup_customer` needs to let her open with 'Hi Mrs. Henderson —
    is this about the exterior job?'"""
    result = await conn.execute(
        text(
            """
            SELECT job_type, created_at, status
            FROM leads
            WHERE contact_id = :contact_id
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"contact_id": contact_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    return {"job_type": row["job_type"], "when": row["created_at"], "status": row["status"]}


async def untouched(conn: AsyncConnection, *, older_than_hours: int = 24) -> list[LeadRow]:
    """The 'needs you' list. Uses the partial index from 01-SCHEMA.sql."""
    result = await conn.execute(
        text(
            """
            SELECT id, caller_name, job_type, urgency, status, created_at, value_cents
            FROM leads
            WHERE first_touched_at IS NULL
              AND status = 'new'
              AND created_at < now() - make_interval(hours => :hours)
            ORDER BY created_at ASC
            """
        ),
        {"hours": older_than_hours},
    )
    return [_to_row(row) for row in result.mappings()]


def _to_row(row: Any) -> LeadRow:
    return LeadRow(
        id=row["id"],
        caller_name=row["caller_name"],
        job_type=row["job_type"],
        urgency=row["urgency"],
        status=row["status"],
        created_at=row["created_at"],
        value_cents=row["value_cents"],
    )
