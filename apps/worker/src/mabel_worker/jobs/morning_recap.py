"""The 7am text. Phase 3's whole reason for existing.

04-REPO.md: "Done when Sam's own phone gets a useful 7am text."

Enqueued hourly by `pg_cron`, which picks off whichever tenants have just hit
7am local. This job composes the message and queues it; `send_notification`
delivers it. That split means a composition bug and a delivery outage look
different in the notifications table, which matters at 7am when somebody is
asking why the text did not arrive.

**Quiet hours do not apply.** 7am local is not quiet hours by construction —
it is computed from the tenant's own timezone. The quiet-hours override exists
for emergencies, which is a different path.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from mabel_db.queries.notifications import enqueue
from mabel_db.tenant import tenant_scope
from mabel_sms.compose import RecapLead, morning_recap
from mabel_worker.queue import Job

logger = logging.getLogger(__name__)

# What counts as overnight. Generous, because a call at 6pm the previous
# evening is still something he has not seen.
LOOKBACK_HOURS = 14


async def run(job: Job, engine: AsyncEngine) -> None:
    if job.tenant_id is None:
        raise ValueError("morning_recap needs a tenant")

    async with tenant_scope(job.tenant_id, engine=engine) as conn:
        tenant = await _tenant(conn)
        if tenant is None:
            # The tenant was deleted between cron enqueueing and the worker
            # picking it up. Not an error.
            logger.info("tenant %s is gone; skipping the recap", job.tenant_id)
            return

        zone = ZoneInfo(tenant["timezone"])
        local_now = datetime.now(UTC).astimezone(zone)
        since = datetime.now(UTC) - timedelta(hours=LOOKBACK_HOURS)

        leads = await _overnight_leads(conn, since)
        calls_answered, emergencies = await _overnight_counts(conn, since)

        recipients = await _recap_recipients(conn)
        if not recipients:
            # Nobody has opted in. Worth a log, not an error: a tenant may
            # deliberately live in the portal instead.
            logger.info("tenant %s has nobody set for the recap", job.tenant_id)
            return

        body = morning_recap(
            business_name=tenant["business_name"],
            leads=leads,
            emergencies=emergencies,
            calls_answered=calls_answered,
            local_day=local_now.strftime("%a"),
        )

        for person in recipients:
            await enqueue(
                conn,
                tenant_id=job.tenant_id,
                kind="morning_recap",
                channel="sms",
                to_address=person["phone_e164"],
                body=body,
                user_id=person["id"],
            )

        # The list he is replying to when he texts back "1". Held for 24 hours,
        # matching the sms_sessions TTL in the schema.
        await _remember_list(conn, job.tenant_id, recipients, leads)


async def _tenant(conn: AsyncConnection) -> dict[str, Any] | None:
    result = await conn.execute(
        text("SELECT business_name, timezone FROM tenants WHERE deleted_at IS NULL")
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def _overnight_leads(conn: AsyncConnection, since: datetime) -> list[RecapLead]:
    """Leads from overnight, emergencies first.

    `value_cents` is selected and carried because the owner entered it himself
    and is entitled to see it in his own recap. It is never computed here and
    never touched by a model.
    """
    result = await conn.execute(
        text(
            """
            SELECT caller_name, job_type, urgency, callback_e164, created_at, value_cents
            FROM leads
            WHERE created_at >= :since
            ORDER BY
              CASE urgency WHEN 'emergency' THEN 0 WHEN 'soon' THEN 1 ELSE 2 END,
              created_at
            """
        ),
        {"since": since},
    )
    return [
        RecapLead(
            name=row["caller_name"],
            job_type=row["job_type"],
            urgency=row["urgency"],
            phone_e164=row["callback_e164"],
            at=row["created_at"],
            value_cents=row["value_cents"],
        )
        for row in result.mappings()
    ]


async def _overnight_counts(conn: AsyncConnection, since: datetime) -> tuple[int, int]:
    result = await conn.execute(
        text(
            """
            SELECT count(*) AS answered,
                   count(*) FILTER (WHERE outcome = 'emergency') AS emergencies
            FROM calls
            WHERE started_at >= :since
            """
        ),
        {"since": since},
    )
    row = result.mappings().one()
    return int(row["answered"]), int(row["emergencies"])


async def _recap_recipients(conn: AsyncConnection) -> list[dict[str, Any]]:
    result = await conn.execute(
        text(
            """
            SELECT id, phone_e164
            FROM users
            WHERE notify_recap
              AND deleted_at IS NULL
              AND phone_e164 IS NOT NULL
            """
        )
    )
    return [dict(row) for row in result.mappings()]


async def _remember_list(
    conn: AsyncConnection,
    tenant_id: UUID,
    recipients: list[dict[str, Any]],
    leads: list[RecapLead],
) -> None:
    """Store what each recipient was shown, so `1` means something.

    Per phone number, because that is the key `sms_sessions` is unique on and
    it is what an inbound message arrives with.
    """
    import json

    shown = [
        {
            "name": lead.name,
            "job_type": lead.job_type,
            "phone": lead.phone_e164,
            "at": lead.at.isoformat(),
        }
        for lead in leads[:3]
    ]

    for person in recipients:
        await conn.execute(
            text(
                """
                INSERT INTO sms_sessions (tenant_id, user_id, phone_e164, context, expires_at)
                VALUES (:tenant_id, :user_id, :phone, cast(:context as jsonb),
                        now() + interval '24 hours')
                ON CONFLICT (phone_e164) DO UPDATE
                  SET context = excluded.context,
                      expires_at = excluded.expires_at,
                      updated_at = now()
                """
            ),
            {
                "tenant_id": tenant_id,
                "user_id": person["id"],
                "phone": person["phone_e164"],
                "context": json.dumps({"last_list": shown, "kind": "morning_recap"}),
            },
        )
