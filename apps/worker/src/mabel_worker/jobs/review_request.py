"""Asking for a review, two days after the owner said the job was won.

Home service businesses are chosen off a search results page, and what that
page sorts by is review count. A shop doing good work with eleven reviews loses
to a worse one with two hundred. Asking is the entire difference, and nobody
running a truck all day remembers to ask.

**Hung off the sweep, not off the write.** `WON RUIZ 3800` by text and marking
a lead won in the portal are two code paths, and a third will exist the moment
Jobber's webhook lands. A sweep that looks at won leads catches all of them and
is idempotent besides, which a write hook is not: the owner who marks a lead
won, then lost, then won again should not generate three requests.

**Two days, not two hours.** The lead is marked won when the money is agreed,
which is usually before the work is done. Asking a customer to review a job
that has not happened yet is worse than not asking.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from mabel_db.queries.customer_sms import enqueue_to_customer
from mabel_db.tenant import tenant_scope
from mabel_sms.customer import review_request
from mabel_worker.queue import Job

logger = logging.getLogger(__name__)

# Once per lead, forever. `enqueue_to_customer` refuses an opted-out contact,
# but nothing there stops the same won job being asked about twice, and being
# asked twice for a review is how someone opts out.
ALREADY_ASKED = """
  SELECT 1 FROM notifications
  WHERE kind = 'customer_review' AND lead_id = :lead_id
"""


async def run(job: Job, engine: AsyncEngine) -> None:
    if job.tenant_id is None:
        raise ValueError("review_request needs a tenant")

    raw_lead_id = job.payload.get("lead_id")
    if not raw_lead_id:
        raise ValueError("review_request needs a lead_id in its payload")
    lead_id = UUID(str(raw_lead_id))

    async with tenant_scope(job.tenant_id, engine=engine) as conn:
        settings = await _settings(conn)
        if not settings["review_requests_enabled"] or not settings["review_url"]:
            # Switched off, or switched on without a link, between the sweep
            # and now. Not an error -- a shop turning this off mid-sweep is
            # exactly the case where sending anyway would be the bug.
            return

        lead = await _still_won(conn, lead_id)
        if lead is None:
            # Reopened, lost, or deleted since the sweep. The owner changing
            # their mind is the normal case, not a failure.
            return
        if lead["contact_id"] is None:
            # A won job with no contact has no one to ask. Happens when the
            # caller withheld their number.
            return

        already = await conn.execute(text(ALREADY_ASKED), {"lead_id": lead_id})
        if already.first() is not None:
            return

        body = review_request(
            business_name=settings["business_name"],
            review_url=settings["review_url"],
            customer_name=_first_name(lead["caller_name"]),
        )
        # The gate is inside this, and re-runs. Two days have passed since the
        # decision to ask, which is plenty of time to have replied STOP.
        queued = await enqueue_to_customer(
            conn,
            tenant_id=job.tenant_id,
            contact_id=lead["contact_id"],
            kind="customer_review",
            body=body,
            lead_id=lead_id,
        )
        if queued is None:
            logger.info("no review request for lead %s; the gate refused", lead_id)


async def _settings(conn: AsyncConnection) -> dict[str, Any]:
    result = await conn.execute(
        text("SELECT business_name, review_requests_enabled, review_url FROM tenants")
    )
    return dict(result.mappings().one())


async def _still_won(conn: AsyncConnection, lead_id: UUID) -> dict[str, Any] | None:
    result = await conn.execute(
        text(
            """
            SELECT contact_id, caller_name
            FROM leads
            WHERE id = :lead_id AND status = 'won'
            """
        ),
        {"lead_id": lead_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


def _first_name(caller_name: str | None) -> str | None:
    """ "Dana", not "Dana Ruiz".

    A text using someone's full name reads like a collections notice. It also
    reads like a mail merge, which is what this is, and the whole point of the
    message is that it should not.
    """
    if not caller_name:
        return None
    first = caller_name.strip().split()[0]
    return first if first else None
