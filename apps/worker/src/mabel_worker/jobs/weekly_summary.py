"""Monday morning. The week in one message.

The only recurring message that carries a total, and it is a sum of integer
cents the owner entered by hand. No model is anywhere near it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabel_db.queries.notifications import enqueue
from mabel_db.tenant import tenant_scope
from mabel_sms.compose import weekly_summary
from mabel_worker.queue import Job

logger = logging.getLogger(__name__)


async def run(job: Job, engine: AsyncEngine) -> None:
    if job.tenant_id is None:
        raise ValueError("weekly_summary needs a tenant")

    since = datetime.now(UTC) - timedelta(days=7)

    async with tenant_scope(job.tenant_id, engine=engine) as conn:
        totals = await conn.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM calls WHERE started_at >= :since) AS calls_answered,
                  (SELECT count(*) FROM leads WHERE created_at >= :since) AS leads_created,
                  (SELECT count(*) FROM calls
                     WHERE started_at >= :since AND outcome = 'emergency') AS emergencies,
                  (SELECT count(*) FROM leads WHERE won_at >= :since) AS jobs_won,
                  (SELECT coalesce(sum(value_cents), 0) FROM leads
                     WHERE won_at >= :since) AS won_value_cents
                """
            ),
            {"since": since},
        )
        row = totals.mappings().one()

        body = weekly_summary(
            calls_answered=int(row["calls_answered"]),
            leads_created=int(row["leads_created"]),
            emergencies=int(row["emergencies"]),
            jobs_won=int(row["jobs_won"]),
            # coalesce'd to 0 in SQL, so this is always an integer number of
            # cents and never a NULL that would format as "None".
            won_value_cents=int(row["won_value_cents"]),
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
                kind="weekly_summary",
                channel="sms",
                to_address=person["phone_e164"],
                body=body,
                user_id=person["id"],
            )
