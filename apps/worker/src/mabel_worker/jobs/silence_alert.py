"""A tenant who used to get calls and now gets none.

This is the churn catcher, and it is worth more than it looks. A contractor
whose call forwarding got switched off — a carrier change, a new handset, a
wrong code — stops getting calls and concludes Mabel does not work. He will
blame us before he checks his phone settings, and he will not open a ticket.
He will just cancel.

The cron only enqueues for tenants with prior traffic and none for seven days,
so a brand new tenant never trips it.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabel_db.queries.notifications import enqueue
from mabel_db.tenant import tenant_scope
from mabel_sms.compose import silence_alert
from mabel_worker.queue import Job

logger = logging.getLogger(__name__)

# Not more than once a week. It is an alarming message, and repeating it daily
# turns it into noise.
RECENTLY_ALERTED = """
  SELECT 1 FROM notifications
  WHERE kind = 'system'
    AND body LIKE 'No calls have reached Mabel%'
    AND created_at > now() - interval '7 days'
"""


async def run(job: Job, engine: AsyncEngine) -> None:
    if job.tenant_id is None:
        raise ValueError("silence_alert needs a tenant")

    async with tenant_scope(job.tenant_id, engine=engine) as conn:
        if (await conn.execute(text(RECENTLY_ALERTED))).first() is not None:
            return

        tenant = await conn.execute(
            text("SELECT business_name FROM tenants WHERE deleted_at IS NULL")
        )
        row = tenant.mappings().one_or_none()
        if row is None:
            return

        quiet = await conn.execute(
            text("SELECT extract(day FROM now() - max(started_at))::int AS days FROM calls")
        )
        days = quiet.scalar_one() or 7

        body = silence_alert(business_name=row["business_name"], days_quiet=int(days))

        # The owner specifically. An office manager cannot fix call forwarding
        # on the owner's mobile.
        recipients = await conn.execute(
            text(
                "SELECT id, phone_e164 FROM users "
                "WHERE deleted_at IS NULL AND phone_e164 IS NOT NULL AND role = 'owner'"
            )
        )
        for person in recipients.mappings():
            await enqueue(
                conn,
                tenant_id=job.tenant_id,
                kind="system",
                channel="sms",
                to_address=person["phone_e164"],
                body=body,
                user_id=person["id"],
            )
