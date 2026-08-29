"""The consent gate, against a real database.

Two things are being checked and they are different in kind.

The first is that the four conditions in `may_text` actually refuse. That could
be tested with a mock and would prove less, because the interesting failures
here are SQL ones -- a join that drops the row, a null that compares wrong.

The second is that the gate is not bypassable. `enqueue_to_customer` re-runs
the decision rather than trusting one made earlier, which matters because a
review request is queued days after the job it follows and the contact may have
said STOP in between. That is a race, and the only honest way to test a race is
to arrange it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabel_db.queries.customer_sms import (
    enqueue_to_customer,
    may_text,
    opt_out,
    record_consent,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def shop_with_contact(engine: AsyncEngine):
    """One tenant with customer SMS on, and one contact who has called us.

    Seeded through the owner engine: seeding through the app role is what the
    rest of this directory is testing, and doing it here would make a failure
    ambiguous between the gate and the policy.
    """
    tenant_id, contact_id = uuid4(), None
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, business_name, trade, did_e164, status, "
                "customer_sms_enabled) "
                "VALUES (:id, 'Ruiz Plumbing', 'plumbing', :did, 'active', true)"
            ),
            {"id": tenant_id, "did": "+12165550148"},
        )
        result = await conn.execute(
            text(
                "INSERT INTO contacts (tenant_id, display_name, primary_phone, sms_consent_at) "
                "VALUES (:t, 'Dana Ruiz', '+12165550001', now()) RETURNING id"
            ),
            {"t": tenant_id},
        )
        contact_id = result.scalar_one()

    yield tenant_id, contact_id

    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant_id})


class TestTheGateRefuses:
    async def test_a_contact_who_called_us_may_be_texted(self, app_engine, shop_with_contact):
        tenant_id, contact_id = shop_with_contact
        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
            decision = await may_text(conn, contact_id)
        assert decision.allowed
        assert decision.phone_e164 == "+12165550001"
        assert decision.business_name == "Ruiz Plumbing"
        # Nothing has been sent yet, so the opt-out footer is still owed.
        assert decision.first_contact

    async def test_a_tenant_with_the_switch_off_sends_nothing(
        self, engine, app_engine, shop_with_contact
    ):
        """Off by default, and the default is the one that matters: a tenant
        with no 10DLC campaign has messages accepted by the API and dropped by
        the carrier, and never finds out."""
        tenant_id, contact_id = shop_with_contact
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE tenants SET customer_sms_enabled = false WHERE id = :id"),
                {"id": tenant_id},
            )
        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
            decision = await may_text(conn, contact_id)
            queued = await enqueue_to_customer(
                conn,
                tenant_id=tenant_id,
                contact_id=contact_id,
                kind="customer_confirmation",
                body="hello",
            )
        assert decision.reason == "disabled"
        assert queued is None

    async def test_a_contact_who_never_called_us_is_not_texted(
        self, engine, app_engine, shop_with_contact
    ):
        """A contact typed into the portal by hand has given us nothing. The
        phone call is the consent; without one there is no basis to text."""
        tenant_id, _ = shop_with_contact
        async with engine.begin() as conn:
            stranger = (
                await conn.execute(
                    text(
                        "INSERT INTO contacts (tenant_id, display_name, primary_phone) "
                        "VALUES (:t, 'Typed In By Hand', '+12165550777') RETURNING id"
                    ),
                    {"t": tenant_id},
                )
            ).scalar_one()
        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
            decision = await may_text(conn, stranger)
        assert decision.reason == "no_consent"

    async def test_stop_is_permanent(self, app_engine, shop_with_contact):
        tenant_id, contact_id = shop_with_contact
        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
            await opt_out(conn, contact_id)
            assert (await may_text(conn, contact_id)).reason == "opted_out"

            # Calling again is consent to that call, not permission to text.
            # Someone who said STOP and later phones about a different job is
            # asked in the portal, not re-enrolled by a side effect.
            await record_consent(conn, contact_id)
            assert (await may_text(conn, contact_id)).reason == "opted_out"

    async def test_a_deleted_contact_is_not_texted(self, engine, app_engine, shop_with_contact):
        tenant_id, contact_id = shop_with_contact
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE contacts SET deleted_at = now() WHERE id = :id"), {"id": contact_id}
            )
        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
            assert (await may_text(conn, contact_id)).reason == "unknown"


class TestTheGateIsNotBypassable:
    async def test_the_decision_is_rechecked_at_queue_time(self, app_engine, shop_with_contact):
        """The review-request race, arranged rather than argued about.

        A decision taken when the owner marked the job won is acted on days
        later. In between, the contact replied STOP. `enqueue_to_customer`
        re-runs the gate rather than trusting the passed-in decision, so the
        message is never written.
        """
        tenant_id, contact_id = shop_with_contact
        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
            earlier = await may_text(conn, contact_id)
            assert earlier.allowed  # the decision the caller is holding

            await opt_out(conn, contact_id)  # what happened in between

            queued = await enqueue_to_customer(
                conn,
                tenant_id=tenant_id,
                contact_id=contact_id,
                kind="customer_review",
                body="thanks for your business",
                scheduled_for=datetime.now(UTC) + timedelta(days=2),
            )
        assert queued is None

    async def test_a_queued_message_marks_the_contact_as_no_longer_new(
        self, app_engine, shop_with_contact
    ):
        """`first_contact` is derived from what was sent, not from a flag on the
        contact. A flag would be set by the sender and would therefore be wrong
        exactly once -- on the send that crashed between queueing and flagging."""
        tenant_id, contact_id = shop_with_contact
        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
            assert (await may_text(conn, contact_id)).first_contact
            await enqueue_to_customer(
                conn,
                tenant_id=tenant_id,
                contact_id=contact_id,
                kind="customer_confirmation",
                body="hello",
            )
            assert not (await may_text(conn, contact_id)).first_contact

    async def test_consent_keeps_the_first_timestamp(self, app_engine, shop_with_contact):
        """The question a regulator asks is when consent was obtained. The
        answer should not move every time someone phones again."""
        tenant_id, contact_id = shop_with_contact
        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
            before = (
                await conn.execute(
                    text("SELECT sms_consent_at FROM contacts WHERE id = :id"), {"id": contact_id}
                )
            ).scalar_one()
            await record_consent(conn, contact_id, at=datetime.now(UTC) + timedelta(days=1))
            after = (
                await conn.execute(
                    text("SELECT sms_consent_at FROM contacts WHERE id = :id"), {"id": contact_id}
                )
            ).scalar_one()
        assert before == after


class TestStopCrossesTenants:
    async def test_one_number_at_two_shops_is_silenced_at_both(self, engine, app_engine):
        """A homeowner may use a plumber and a roofer who both run Mabel.

        `resolve_contacts_by_phone` returns every match precisely so that STOP
        can be honoured at all of them. Silencing one and not the other leaves
        someone who asked to be left alone still receiving messages, which is
        the failure that carries a fine.
        """
        plumber, roofer = uuid4(), uuid4()
        async with engine.begin() as conn:
            for tenant_id, name, did in (
                (plumber, "Ruiz Plumbing", "+12165550148"),
                (roofer, "Delgado Roofing", "+12165550199"),
            ):
                await conn.execute(
                    text(
                        "INSERT INTO tenants (id, business_name, trade, did_e164, status, "
                        "customer_sms_enabled) VALUES (:id, :n, 'plumbing', :d, 'active', true)"
                    ),
                    {"id": tenant_id, "n": name, "d": did},
                )
                await conn.execute(
                    text(
                        "INSERT INTO contacts (tenant_id, display_name, primary_phone, "
                        "sms_consent_at) VALUES (:t, 'Dana', '+12165550001', now())"
                    ),
                    {"t": tenant_id},
                )

        try:
            async with app_engine.begin() as conn:
                found = await conn.execute(
                    text("SELECT tenant_id, contact_id FROM resolve_contacts_by_phone(:p)"),
                    {"p": "+12165550001"},
                )
                matches = [dict(row) for row in found.mappings()]
            assert {m["tenant_id"] for m in matches} == {plumber, roofer}

            for match in matches:
                async with app_engine.begin() as conn:
                    await conn.execute(
                        # SET LOCAL is not a planned statement and takes no
                        # bind parameters. These ids are uuid4s made above.
                        text(f"SET LOCAL app.tenant_id = '{match['tenant_id']}'")
                    )
                    await opt_out(conn, match["contact_id"])

            for match in matches:
                async with app_engine.begin() as conn:
                    await conn.execute(
                        # SET LOCAL is not a planned statement and takes no
                        # bind parameters. These ids are uuid4s made above.
                        text(f"SET LOCAL app.tenant_id = '{match['tenant_id']}'")
                    )
                    assert (await may_text(conn, match["contact_id"])).reason == "opted_out"
        finally:
            async with engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM tenants WHERE id = ANY(:ids)"), {"ids": [plumber, roofer]}
                )

    async def test_the_resolver_runs_before_any_tenant_context(self, app_engine):
        """The whole reason it is SECURITY DEFINER. STOP arrives with a phone
        number and nothing else -- there is no tenant to scope to yet, which is
        exactly what is being resolved."""
        async with app_engine.begin() as conn:
            # No `SET LOCAL app.tenant_id` anywhere above this line.
            result = await conn.execute(
                text("SELECT count(*) FROM resolve_contacts_by_phone('+19995550000')")
            )
            assert result.scalar_one() == 0
