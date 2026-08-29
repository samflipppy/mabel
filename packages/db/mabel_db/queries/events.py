"""The communication thread. Append-only.

`communication_events` is never updated and never deleted. It is what turns
Mabel from an answering service into a system of record: every call, every SMS
both directions, every note and status change, in one chronological list per
contact.

Append-only is not fussiness. The thread is the thing the office manager
trusts. A row that can be edited after the fact is a row somebody will edit
after the fact, and then the thread stops being evidence of what happened.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def append(
    conn: AsyncConnection,
    *,
    tenant_id: UUID,
    kind: str,
    occurred_at: datetime | None = None,
    contact_id: UUID | None = None,
    lead_id: UUID | None = None,
    direction: str | None = None,
    body: str | None = None,
    payload: dict[str, Any] | None = None,
    storage_path: str | None = None,
    actor_user_id: UUID | None = None,
) -> UUID:
    """One interaction, one row.

    There is deliberately no `update_event` in this module. If something was
    recorded wrongly, the correction is another row.
    """
    import json

    result = await conn.execute(
        text(
            """
            INSERT INTO communication_events
              (tenant_id, contact_id, lead_id, kind, direction, occurred_at,
               body, payload, storage_path, actor_user_id)
            VALUES
              (:tenant_id, :contact_id, :lead_id, :kind, :direction,
               coalesce(:occurred_at, now()), :body, cast(:payload as jsonb),
               :storage_path, :actor_user_id)
            RETURNING id
            """
        ),
        {
            "tenant_id": tenant_id,
            "contact_id": contact_id,
            "lead_id": lead_id,
            "kind": kind,
            "direction": direction,
            "occurred_at": occurred_at,
            "body": body,
            "payload": json.dumps(payload or {}),
            "storage_path": storage_path,
            "actor_user_id": actor_user_id,
        },
    )
    return result.scalar_one()


async def thread_for_contact(
    conn: AsyncConnection, contact_id: UUID, *, limit: int = 200
) -> list[dict[str, Any]]:
    """The unified thread, newest first. Drives the Customers screen.

    Uses `ix_events_thread`, which leads with tenant_id and orders by
    occurred_at descending — the exact shape of this query.
    """
    result = await conn.execute(
        text(
            """
            SELECT id, kind, direction, occurred_at, body, payload, storage_path, lead_id
            FROM communication_events
            WHERE contact_id = :contact_id
            ORDER BY occurred_at DESC
            LIMIT :limit
            """
        ),
        {"contact_id": contact_id, "limit": limit},
    )
    return [dict(row) for row in result.mappings()]


async def thread_for_lead(conn: AsyncConnection, lead_id: UUID) -> list[dict[str, Any]]:
    result = await conn.execute(
        text(
            """
            SELECT id, kind, direction, occurred_at, body, payload, storage_path
            FROM communication_events
            WHERE lead_id = :lead_id
            ORDER BY occurred_at DESC
            """
        ),
        {"lead_id": lead_id},
    )
    return [dict(row) for row in result.mappings()]


async def open_items(conn: AsyncConnection, contact_id: UUID) -> list[dict[str, Any]]:
    """The dropped-ball surfacing: an inbound message with nothing outbound
    after it. 02-PORTAL.md pins this at the top of the thread — 'Asked about a
    color change Apr 18 — no reply.'

    Inbound events with no outbound event later in the thread. Deliberately
    simple: an owner who replied by phone from his own mobile will show a false
    positive here, and a false positive costs him a glance while a false
    negative costs him the job.
    """
    result = await conn.execute(
        text(
            """
            SELECT e.id, e.kind, e.occurred_at, e.body
            FROM communication_events e
            WHERE e.contact_id = :contact_id
              AND e.direction = 'inbound'
              AND NOT EXISTS (
                SELECT 1 FROM communication_events later
                WHERE later.contact_id = e.contact_id
                  AND later.direction = 'outbound'
                  AND later.occurred_at > e.occurred_at)
            ORDER BY e.occurred_at DESC
            """
        ),
        {"contact_id": contact_id},
    )
    return [dict(row) for row in result.mappings()]
