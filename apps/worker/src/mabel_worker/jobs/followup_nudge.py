"""A lead nobody has touched in 24 hours.

The cron sweep enqueues one job per untouched lead, hourly. This re-checks that
the lead is *still* untouched before sending, because the owner may have called
them in the meantime — and a nudge about a lead he has already dealt with is
how he learns to ignore nudges.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from mabel_db.queries.notifications import enqueue
from mabel_db.tenant import tenant_scope
from mabel_sms.compose import RecapLead, followup_nudge
from mabel_worker.queue import Job

logger = logging.getLogger(__name__)

# Once per lead. A second nudge about the same lead is nagging.
ALREADY_NUDGED = """
  SELECT 1 FROM notifications
  WHERE kind = 'followup_nudge' AND lead_id = :lead_id
"""


async def run(job: Job, engine: AsyncEngine) -> None:
    if job.tenant_id is None:
        raise ValueError("followup_nudge needs a tenant")

    raw_lead_id = job.payload.get("lead_id")
    if not raw_lead_id:
        raise ValueError("followup_nudge needs a lead_id in its payload")
    lead_id = UUID(str(raw_lead_id))

    async with tenant_scope(job.tenant_id, engine=engine) as conn:
        lead = await _still_untouched(conn, lead_id)
        if lead is None:
            # Touched since the sweep enqueued this, or gone. Not an error.
            return

        already = await conn.execute(text(ALREADY_NUDGED), {"lead_id": lead_id})
        if already.first() is not None:
            return

        body = followup_nudge(
            RecapLead(
                name=lead["caller_name"],
                job_type=lead["job_type"],
                urgency=lead["urgency"],
                phone_e164=lead["callback_e164"],
                at=lead["created_at"],
            ),
            hours=int(lead["age_hours"]),
        )

        recipients = await conn.execute(
            text(
                "SELECT id, phone_e164 FROM users "
                "WHERE notify_recap AND deleted_at IS NULL AND phone_e164 IS NOT NULL"
            )
        )
        for person in recipients.mappings():
            await enqueue(
                conn,
                tenant_id=job.tenant_id,
                kind="followup_nudge",
                channel="sms",
                to_address=person["phone_e164"],
                body=body,
                user_id=person["id"],
                lead_id=lead_id,
            )


async def _still_untouched(conn: AsyncConnection, lead_id: UUID) -> dict[str, Any] | None:
    result = await conn.execute(
        text(
            """
            SELECT caller_name, job_type, urgency, callback_e164, created_at,
                   extract(epoch FROM now() - created_at) / 3600 AS age_hours
            FROM leads
            WHERE id = :lead_id
              AND first_touched_at IS NULL
              AND status = 'new'
            """
        ),
        {"lead_id": lead_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None
