"""The loop back to the person who called.

`test_end_to_end.py` walks one night from the shop's side: a call arrives, a
lead is written, the owner is woken, the owner texts back. Nothing in it ever
speaks to the customer, because until now nothing could.

This walks the same night from the customer's side. She calls at 11pm with a
burst pipe, gets a text confirming what we heard, replies to it, and her reply
lands in front of the owner. Then the other shop's identical caller gets
nothing, because that shop has not turned it on.

Doubles are used only where an account does not exist yet: storage and the
Telnyx client. The database, the consent gate, the templates and the routing
are all real.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabel_db.queries import contacts as contacts_q
from mabel_db.queries import leads as leads_q
from mabel_db.queries.customer_sms import may_text, record_consent
from mabel_db.queries.notifications import enqueue_emergency
from mabel_media.postcall import CallOutcome, finalize

pytestmark = pytest.mark.asyncio

DID = "+12165550148"
CALLER = "+12165550100"
OWNER_PHONE = "+12165550111"


@pytest_asyncio.fixture
async def texting_shop(app_engine: AsyncEngine, engine: AsyncEngine):
    """A shop that has switched customer SMS on.

    Switched on explicitly, because the column defaults to false and that
    default is load bearing -- see the last test in this file.
    """
    tenant_id = uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, business_name, trade, timezone, status, did_e164, "
                "customer_sms_enabled) "
                "VALUES (:id, 'Ruiz Plumbing', 'plumbing', 'America/New_York', 'active', :did, "
                "true)"
            ),
            {"id": tenant_id, "did": DID},
        )
        await conn.execute(
            text(
                "INSERT INTO users (tenant_id, email, full_name, phone_e164, role, "
                "notify_emergencies, notify_recap) "
                "VALUES (:t, 'ray@ruiz.example', 'Ray Ruiz', :phone, 'owner', true, true)"
            ),
            {"t": tenant_id, "phone": OWNER_PHONE},
        )
        await conn.execute(
            text(
                """
                INSERT INTO agent_configs
                  (tenant_id, version, is_live, greeting, business_hours, services)
                VALUES (:t, 1, true, 'Thanks for calling Ruiz Plumbing.',
                        cast(:hours as jsonb), ARRAY['drain cleaning'])
                """
            ),
            {"t": tenant_id, "hours": json.dumps({"mon": {"open": "08:00", "close": "17:00"}})},
        )
    yield tenant_id
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant_id})


async def _queued(conn, tenant_id, kind: str) -> list[str]:
    result = await conn.execute(
        text("SELECT body FROM notifications WHERE kind = :k ORDER BY created_at"), {"k": kind}
    )
    return [row[0] for row in result]


class TestSheHearsBack:
    """One caller, 11pm, burst pipe. Each test builds on the last."""

    async def test_a_routine_call_gets_a_confirmation_naming_what_we_heard(
        self, app_engine, texting_shop
    ):
        """The read-back is the point. `job_type` and `service_address` are the
        two things a caller worries an AI got wrong."""
        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{texting_shop}'"))
            contact, _ = await contacts_q.resolve_or_create(
                conn, tenant_id=texting_shop, phone_e164=CALLER, name="Dana Ruiz"
            )
            await record_consent(conn, contact.id)
            lead_id = await leads_q.create(
                conn,
                tenant_id=texting_shop,
                contact_id=contact.id,
                call_id=None,
                caller_name="Dana Ruiz",
                callback_e164=CALLER,
                service_address="44 Elm St",
                job_type="clogged kitchen drain",
                description="water backing up",
                urgency="routine",
                source="call",
            )

        started = datetime.now(UTC) - timedelta(minutes=3)
        await finalize(
            CallOutcome(
                call_id=f"xai-{uuid4()}",
                tenant_id=texting_shop,
                timezone="America/New_York",
                trade="plumbing",
                from_e164=CALLER,
                to_e164=DID,
                started_at=started,
                ended_at=started + timedelta(minutes=3),
                turns=[{"role": "caller", "text": "my kitchen drain is backed up"}],
                lead_id=lead_id,
                contact_id=contact.id,
            ),
            engine=app_engine,
        )

        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{texting_shop}'"))
            sent = await _queued(conn, texting_shop, "customer_confirmation")
        assert len(sent) == 1
        assert "Ruiz Plumbing" in sent[0]
        assert "clogged kitchen drain" in sent[0]
        assert "44 Elm St" in sent[0]
        assert "STOP" in sent[0]  # her first message from us

    async def test_an_emergency_says_the_tech_was_alerted_only_when_one_was(
        self, app_engine, texting_shop
    ):
        """The two wordings differ on a claim about the world, so the code reads
        what `enqueue_emergency` actually wrote rather than assuming."""
        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{texting_shop}'"))
            contact, _ = await contacts_q.resolve_or_create(
                conn, tenant_id=texting_shop, phone_e164=CALLER, name="Dana Ruiz"
            )
            await record_consent(conn, contact.id)
            lead_id = await leads_q.create(
                conn,
                tenant_id=texting_shop,
                contact_id=contact.id,
                call_id=None,
                caller_name="Dana Ruiz",
                callback_e164=CALLER,
                service_address="44 Elm St",
                job_type="burst pipe",
                description="water everywhere",
                urgency="emergency",
                source="call",
                escalated_at=datetime.now(UTC),
            )
            woke_someone = await enqueue_emergency(
                conn, tenant_id=texting_shop, body="EMERGENCY: burst pipe", lead_id=lead_id
            )
        assert woke_someone  # the owner has notify_emergencies

        started = datetime.now(UTC) - timedelta(minutes=2)
        await finalize(
            CallOutcome(
                call_id=f"xai-{uuid4()}",
                tenant_id=texting_shop,
                timezone="America/New_York",
                trade="plumbing",
                from_e164=CALLER,
                to_e164=DID,
                started_at=started,
                ended_at=started + timedelta(minutes=2),
                turns=[{"role": "caller", "text": "a pipe burst in my basement"}],
                lead_id=lead_id,
                contact_id=contact.id,
                escalated=True,
            ),
            engine=app_engine,
        )

        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{texting_shop}'"))
            sent = await _queued(conn, texting_shop, "customer_emergency")
        assert len(sent) == 1
        assert "alerted" in sent[0]
        assert "another provider" not in sent[0]
        assert "911" in sent[0]

    async def test_with_nobody_on_call_she_is_told_to_try_elsewhere(
        self, app_engine, engine, texting_shop
    ):
        """The branch that justifies the whole distinction.

        A shop with an empty rotation and no owner taking emergencies has an
        emergency and nobody to send it to. Telling the caller a tech was
        alerted would be a lie told at the worst possible moment.
        """
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE users SET notify_emergencies = false WHERE tenant_id = :t"),
                {"t": texting_shop},
            )

        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{texting_shop}'"))
            contact, _ = await contacts_q.resolve_or_create(
                conn, tenant_id=texting_shop, phone_e164=CALLER, name="Dana Ruiz"
            )
            await record_consent(conn, contact.id)
            lead_id = await leads_q.create(
                conn,
                tenant_id=texting_shop,
                contact_id=contact.id,
                call_id=None,
                caller_name="Dana Ruiz",
                callback_e164=CALLER,
                service_address="44 Elm St",
                job_type="gas smell",
                description="smells like gas",
                urgency="emergency",
                source="call",
                escalated_at=datetime.now(UTC),
            )
            woke_someone = await enqueue_emergency(
                conn, tenant_id=texting_shop, body="EMERGENCY: gas", lead_id=lead_id
            )
        assert not woke_someone

        started = datetime.now(UTC) - timedelta(minutes=1)
        await finalize(
            CallOutcome(
                call_id=f"xai-{uuid4()}",
                tenant_id=texting_shop,
                timezone="America/New_York",
                trade="plumbing",
                from_e164=CALLER,
                to_e164=DID,
                started_at=started,
                ended_at=started + timedelta(minutes=1),
                turns=[{"role": "caller", "text": "I smell gas"}],
                lead_id=lead_id,
                contact_id=contact.id,
                escalated=True,
            ),
            engine=app_engine,
        )

        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{texting_shop}'"))
            sent = await _queued(conn, texting_shop, "customer_emergency")
        assert len(sent) == 1
        assert "another provider" in sent[0]
        assert "alerted" not in sent[0]

    async def test_the_second_message_drops_the_opt_out_footer(self, app_engine, texting_shop):
        """A fifth of every segment, saved on every message after the first."""
        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{texting_shop}'"))
            contact, _ = await contacts_q.resolve_or_create(
                conn, tenant_id=texting_shop, phone_e164=CALLER, name="Dana Ruiz"
            )
            await record_consent(conn, contact.id)
            assert (await may_text(conn, contact.id)).first_contact

            await conn.execute(
                text(
                    "INSERT INTO notifications (tenant_id, kind, channel, to_address, body) "
                    "VALUES (:t, 'customer_confirmation', 'sms', :p, 'earlier message')"
                ),
                {"t": texting_shop, "p": CALLER},
            )
            assert not (await may_text(conn, contact.id)).first_contact


class TestTheSwitchIsOffByDefault:
    async def test_a_shop_that_has_not_turned_it_on_texts_nobody(self, app_engine, engine):
        """The default is false and it protects the tenant from themselves.

        Without a registered 10DLC campaign these messages are accepted by the
        API and dropped by the carrier, and the shop never finds out. Turning
        it on has to be a deliberate act taken after the campaign exists.
        """
        tenant_id = uuid4()
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, business_name, trade, status, did_e164) "
                    "VALUES (:id, 'Delgado HVAC', 'hvac', 'active', '+12165550199')"
                ),
                {"id": tenant_id},
            )
        try:
            async with app_engine.begin() as conn:
                await conn.execute(text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
                contact, _ = await contacts_q.resolve_or_create(
                    conn, tenant_id=tenant_id, phone_e164=CALLER, name="Dana Ruiz"
                )
                await record_consent(conn, contact.id)
                decision = await may_text(conn, contact.id)
            assert decision.reason == "disabled"

            started = datetime.now(UTC) - timedelta(minutes=2)
            await finalize(
                CallOutcome(
                    call_id=f"xai-{uuid4()}",
                    tenant_id=tenant_id,
                    timezone="America/New_York",
                    trade="hvac",
                    from_e164=CALLER,
                    to_e164="+12165550199",
                    started_at=started,
                    ended_at=started + timedelta(minutes=2),
                    turns=[{"role": "caller", "text": "no heat"}],
                    contact_id=contact.id,
                ),
                engine=app_engine,
            )

            async with app_engine.begin() as conn:
                await conn.execute(text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
                result = await conn.execute(
                    text("SELECT count(*) FROM notifications WHERE kind LIKE 'customer_%'")
                )
                assert result.scalar_one() == 0
        finally:
            async with engine.begin() as conn:
                await conn.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant_id})

    async def test_a_caller_who_withheld_their_number_gets_nothing_and_the_call_survives(
        self, app_engine, texting_shop
    ):
        """No contact means no consent record and nowhere to attribute a text.

        The call still archives. A courtesy message is never allowed to be the
        reason a recording is lost.
        """
        started = datetime.now(UTC) - timedelta(minutes=2)
        archived = await finalize(
            CallOutcome(
                call_id=f"xai-{uuid4()}",
                tenant_id=texting_shop,
                timezone="America/New_York",
                trade="plumbing",
                from_e164=None,
                to_e164=DID,
                started_at=started,
                ended_at=started + timedelta(minutes=2),
                turns=[{"role": "caller", "text": "hello?"}],
                contact_id=None,
            ),
            engine=app_engine,
        )
        assert archived.duration_sec == 120

        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{texting_shop}'"))
            result = await conn.execute(
                text("SELECT count(*) FROM notifications WHERE kind LIKE 'customer_%'")
            )
            assert result.scalar_one() == 0


class TestSheRepliesAndTheOwnerSeesIt:
    """A text back that goes nowhere is worse than not sending one.

    `missed_call` says "reply to this text and we will get right back to you",
    which is a promise made on the owner's behalf. These are the tests that
    make it true.
    """

    async def test_her_reply_reaches_whoever_is_on_call(self, app_engine, texting_shop):
        from mabel_api.webhooks.telnyx import _handle_customer_reply

        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{texting_shop}'"))
            await contacts_q.resolve_or_create(
                conn, tenant_id=texting_shop, phone_e164=CALLER, name="Dana Ruiz"
            )

        handled = await _handle_customer_reply(
            from_number=CALLER,
            to_number=DID,
            body_text="yes tomorrow morning works",
            engine=app_engine,
        )
        assert handled

        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{texting_shop}'"))
            to_owner = await conn.execute(
                text("SELECT to_address, body FROM notifications WHERE kind = 'system'")
            )
            rows = [dict(r) for r in to_owner.mappings()]
        assert len(rows) == 1
        assert rows[0]["to_address"] == OWNER_PHONE
        assert "Dana Ruiz" in rows[0]["body"]
        assert "tomorrow morning" in rows[0]["body"]

    async def test_her_reply_lands_in_her_thread(self, app_engine, texting_shop):
        """Next to the call it followed, so the portal shows a conversation
        rather than two unrelated rows."""
        from mabel_api.webhooks.telnyx import _handle_customer_reply

        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{texting_shop}'"))
            contact, _ = await contacts_q.resolve_or_create(
                conn, tenant_id=texting_shop, phone_e164=CALLER, name="Dana Ruiz"
            )

        await _handle_customer_reply(
            from_number=CALLER, to_number=DID, body_text="still leaking", engine=app_engine
        )

        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{texting_shop}'"))
            result = await conn.execute(
                text(
                    "SELECT kind, direction, body FROM communication_events WHERE contact_id = :c"
                ),
                {"c": contact.id},
            )
            rows = [dict(r) for r in result.mappings()]
        assert rows == [{"kind": "sms_in", "direction": "inbound", "body": "still leaking"}]

    async def test_mabel_does_not_answer(self, app_engine, texting_shop):
        """No auto-reply, deliberately.

        Two systems that each reply to every inbound message will text each
        other until a carrier stops them. A human answering a customer's text
        is the product working, not a gap in it.
        """
        from mabel_api.webhooks.telnyx import _handle_customer_reply

        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{texting_shop}'"))
            await contacts_q.resolve_or_create(
                conn, tenant_id=texting_shop, phone_e164=CALLER, name="Dana Ruiz"
            )

        await _handle_customer_reply(
            from_number=CALLER, to_number=DID, body_text="thanks", engine=app_engine
        )

        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{texting_shop}'"))
            back_to_her = await conn.execute(
                text("SELECT count(*) FROM notifications WHERE to_address = :p"), {"p": CALLER}
            )
            assert back_to_her.scalar_one() == 0

    async def test_a_stranger_texting_the_business_line_is_not_a_customer(
        self, app_engine, texting_shop
    ):
        """Someone who texted without ever calling has no contact, no consent
        and no call to reply about. The owner's DID is not a general inbox."""
        from mabel_api.webhooks.telnyx import _handle_customer_reply

        handled = await _handle_customer_reply(
            from_number="+12165559999",
            to_number=DID,
            body_text="do you do septic tanks",
            engine=app_engine,
        )
        assert not handled

    async def test_a_reply_to_a_did_we_do_not_own_goes_nowhere(self, app_engine, texting_shop):
        from mabel_api.webhooks.telnyx import _handle_customer_reply

        handled = await _handle_customer_reply(
            from_number=CALLER, to_number="+19998887777", body_text="hello", engine=app_engine
        )
        assert not handled
