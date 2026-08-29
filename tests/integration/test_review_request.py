"""The review request, against a real database.

The interesting cases are all refusals. Sending the message is one line; the
job exists because of the six situations in which it must not send, and each
one of them is a real thing a shop owner does.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabel_db.queries import contacts as contacts_q
from mabel_db.queries import leads as leads_q
from mabel_db.queries.customer_sms import opt_out, record_consent
from mabel_worker.jobs import review_request
from mabel_worker.queue import Job

pytestmark = pytest.mark.asyncio

DID = "+12165550148"
CALLER = "+12165550100"
REVIEW_URL = "https://g.page/r/ruizplumbing/review"


@pytest_asyncio.fixture
async def won_job(app_engine: AsyncEngine, engine: AsyncEngine):
    """A shop asking for reviews, and one job it won three days ago."""
    tenant_id = uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, business_name, trade, status, did_e164, "
                "customer_sms_enabled, review_requests_enabled, review_url) "
                "VALUES (:id, 'Ruiz Plumbing', 'plumbing', 'active', :did, true, true, :url)"
            ),
            {"id": tenant_id, "did": DID, "url": REVIEW_URL},
        )

    async with app_engine.begin() as conn:
        await conn.execute(text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
        contact, _ = await contacts_q.resolve_or_create(
            conn, tenant_id=tenant_id, phone_e164=CALLER, name="Dana Ruiz"
        )
        await record_consent(conn, contact.id)
        lead_id = await leads_q.create(
            conn,
            tenant_id=tenant_id,
            contact_id=contact.id,
            call_id=None,
            caller_name="Dana Ruiz",
            callback_e164=CALLER,
            service_address="44 Elm St",
            job_type="water heater",
            description="replaced the unit",
            urgency="routine",
            source="call",
        )
        await leads_q.set_status(conn, lead_id, "won", now=datetime.now(UTC) - timedelta(days=3))
        await leads_q.set_value(conn, lead_id, value_cents=380000)

    yield tenant_id, lead_id, contact.id

    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant_id})


def _job(tenant_id, lead_id) -> Job:
    """A queue row as the worker would hand it over. Built directly rather than
    round-tripped through `claim`, because what is under test is the handler."""
    return Job(
        id=1,
        tenant_id=tenant_id,
        kind="review_request",
        payload={"lead_id": str(lead_id)},
        attempts=0,
        max_attempts=5,
        created_at=datetime.now(UTC),
    )


async def _requests(app_engine, tenant_id) -> list[str]:
    async with app_engine.begin() as conn:
        await conn.execute(text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
        result = await conn.execute(
            text("SELECT body FROM notifications WHERE kind = 'customer_review'")
        )
        return [row[0] for row in result]


class TestItAsks:
    async def test_a_won_job_gets_one_request_with_the_link(self, app_engine, won_job):
        tenant_id, lead_id, _ = won_job
        await review_request.run(_job(tenant_id, lead_id), app_engine)

        sent = await _requests(app_engine, tenant_id)
        assert len(sent) == 1
        assert REVIEW_URL in sent[0]
        assert "Ruiz Plumbing" in sent[0]

    async def test_it_uses_a_first_name(self, app_engine, won_job):
        """A text using someone's full name reads like a collections notice."""
        tenant_id, lead_id, _ = won_job
        await review_request.run(_job(tenant_id, lead_id), app_engine)
        sent = await _requests(app_engine, tenant_id)
        assert "Dana" in sent[0]
        assert "Dana Ruiz" not in sent[0]

    async def test_the_request_carries_no_figure(self, app_engine, won_job):
        """The lead has `value_cents` on it -- an owner typed 3800 into a text
        message -- and none of it reaches the customer. What a shop charged is
        between the shop and its books."""
        tenant_id, lead_id, _ = won_job
        await review_request.run(_job(tenant_id, lead_id), app_engine)
        sent = await _requests(app_engine, tenant_id)
        assert "$" not in sent[0]
        assert "3800" not in sent[0]
        assert "3,800" not in sent[0]


class TestItRefuses:
    async def test_it_asks_once(self, app_engine, won_job):
        """Being asked twice for a review is how someone opts out."""
        tenant_id, lead_id, _ = won_job
        await review_request.run(_job(tenant_id, lead_id), app_engine)
        await review_request.run(_job(tenant_id, lead_id), app_engine)
        assert len(await _requests(app_engine, tenant_id)) == 1

    async def test_a_lead_the_owner_reopened_is_not_asked_about(self, app_engine, won_job):
        """The owner changing their mind between the sweep and the send is the
        normal case, not a failure. Two days is a long time."""
        tenant_id, lead_id, _ = won_job
        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
            await leads_q.set_status(conn, lead_id, "lost", lost_reason="changed their mind")

        await review_request.run(_job(tenant_id, lead_id), app_engine)
        assert await _requests(app_engine, tenant_id) == []

    async def test_a_customer_who_said_stop_is_not_asked(self, app_engine, won_job):
        tenant_id, lead_id, contact_id = won_job
        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
            await opt_out(conn, contact_id)

        await review_request.run(_job(tenant_id, lead_id), app_engine)
        assert await _requests(app_engine, tenant_id) == []

    async def test_turning_the_feature_off_stops_it_mid_flight(self, app_engine, engine, won_job):
        """The job is queued by a sweep and run later. A shop that switched the
        feature off in between meant it."""
        tenant_id, lead_id, _ = won_job
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE tenants SET review_requests_enabled = false WHERE id = :id"),
                {"id": tenant_id},
            )

        await review_request.run(_job(tenant_id, lead_id), app_engine)
        assert await _requests(app_engine, tenant_id) == []

    async def test_a_missing_review_link_sends_nothing(self, app_engine, engine, won_job):
        """A review request with no link asks for a favour and gives no way to
        do it. The settings endpoint refuses this combination, and the job
        refuses it again in case a row got there another way."""
        tenant_id, lead_id, _ = won_job
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE tenants SET review_url = NULL WHERE id = :id"), {"id": tenant_id}
            )

        await review_request.run(_job(tenant_id, lead_id), app_engine)
        assert await _requests(app_engine, tenant_id) == []

    async def test_a_won_job_with_no_contact_is_skipped_quietly(self, app_engine, engine, won_job):
        """Happens when the caller withheld their number. There is nobody to
        ask, which is not an error."""
        tenant_id, lead_id, _ = won_job
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE leads SET contact_id = NULL WHERE id = :id"), {"id": lead_id}
            )

        await review_request.run(_job(tenant_id, lead_id), app_engine)
        assert await _requests(app_engine, tenant_id) == []

    async def test_customer_texting_off_blocks_review_requests_too(
        self, app_engine, engine, won_job
    ):
        """A review request is a customer message. The narrower switch cannot
        outlive the broader one."""
        tenant_id, lead_id, _ = won_job
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE tenants SET customer_sms_enabled = false WHERE id = :id"),
                {"id": tenant_id},
            )

        await review_request.run(_job(tenant_id, lead_id), app_engine)
        assert await _requests(app_engine, tenant_id) == []
