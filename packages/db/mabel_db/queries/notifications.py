"""Queueing a message to a human.

Nothing here sends. Every function writes a `notifications` row with
`status='queued'` and lets the worker deliver it. That separation is what makes
an emergency alert survive the media process dying mid-call: the row is
committed inside the same transaction as the lead, so either both exist or
neither does.

Who gets woken is resolved here rather than in the handler, because it depends
on the on-call rotation and the quiet-hours override, and both are tenant data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def oncall_recipients(
    conn: AsyncConnection, *, at: datetime | None = None
) -> list[dict[str, Any]]:
    """Who is on call, in the order they should be tried.

    Falls back to every user with `notify_emergencies` when no rotation is
    active. The fallback matters: a tenant who never configured a rotation must
    still get woken for a burst pipe, and a silent empty list is the worst
    possible answer to 'who do I call'.
    """
    rotation = await conn.execute(
        text(
            """
            SELECT u.id, u.full_name, u.phone_e164
            FROM oncall_schedules s
            CROSS JOIN LATERAL jsonb_array_elements(s.rotation) AS shift
            JOIN users u ON u.id = (shift->>'user_id')::uuid
            WHERE s.is_active
              AND u.deleted_at IS NULL
              AND u.phone_e164 IS NOT NULL
              AND (extract(isodow FROM coalesce(:at, now()))::int)
                  = ANY(ARRAY(SELECT jsonb_array_elements_text(shift->'days')::int))
            """
        ),
        {"at": at},
    )
    people = [dict(row) for row in rotation.mappings()]
    if people:
        return people

    fallback = await conn.execute(
        text(
            """
            SELECT id, full_name, phone_e164
            FROM users
            WHERE notify_emergencies
              AND deleted_at IS NULL
              AND phone_e164 IS NOT NULL
            ORDER BY CASE role WHEN 'owner' THEN 0 ELSE 1 END
            """
        )
    )
    return [dict(row) for row in fallback.mappings()]


async def enqueue(
    conn: AsyncConnection,
    *,
    tenant_id: UUID,
    kind: str,
    channel: str,
    to_address: str,
    body: str,
    user_id: UUID | None = None,
    lead_id: UUID | None = None,
    scheduled_for: datetime | None = None,
) -> UUID:
    """Queue one message. The worker sends it."""
    result = await conn.execute(
        text(
            """
            INSERT INTO notifications
              (tenant_id, user_id, kind, channel, to_address, body, lead_id,
               status, scheduled_for)
            VALUES
              (:tenant_id, :user_id, :kind, :channel, :to_address, :body, :lead_id,
               'queued', :scheduled_for)
            RETURNING id
            """
        ),
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "kind": kind,
            "channel": channel,
            "to_address": to_address,
            "body": body,
            "lead_id": lead_id,
            "scheduled_for": scheduled_for,
        },
    )
    return result.scalar_one()


async def enqueue_emergency(
    conn: AsyncConnection, *, tenant_id: UUID, body: str, lead_id: UUID
) -> bool:
    """Wake whoever is on call. Returns False when there is nobody to wake.

    False is a real answer and the caller must handle it: a tenant with no
    on-call number configured has an emergency and nobody to send it to, and
    Mabel needs to say 'someone will call you back' rather than implying a
    truck is moving.
    """
    people = await oncall_recipients(conn)
    if not people:
        return False

    for person in people:
        await enqueue(
            conn,
            tenant_id=tenant_id,
            kind="emergency",
            channel="sms",
            to_address=person["phone_e164"],
            body=body,
            user_id=person["id"],
            lead_id=lead_id,
        )
    return True
