"""One call, all the way through, against a real database.

This is the test that answers "does the thing work". Everything Sam has not
signed up for is a test double; everything we wrote is real:

    real: Postgres, RLS, the migrations, tenant resolution, the MCP dispatcher,
          all nine tool handlers, the verticals engine, the post-call pass, the
          worker queue, the SMS grammar and router, the portal queries
    double: xAI (FakeXaiClient), Telnyx (FakeTelnyxClient), Stripe
            (FakeStripeClient), Supabase Storage (an in-memory bucket)

The doubles are the ones already shipped in each package for exactly this, not
new stubs written for the test — and none of them pretends an external thing
happened. `FakeTelnyxClient` records what would have been sent; it does not
mark a notification delivered.

The sequence is a real night:

    a burst pipe at 2am
      -> the dialed number resolves to a tenant
      -> Mabel takes the details and escalates
      -> the on-call owner's phone gets a text
      -> the call is archived with a transcript and a cost
      -> 7am, the recap is composed and queued
      -> the owner texts back WON HENDERSON 3800
      -> the portal shows the money
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabel_api.sms_router import handle, resolve_sender
from mabel_db.queries.config import tenant_by_did
from mabel_db.tenant import admin_scope, tenant_scope
from mabel_mcp.registry import dispatch
from mabel_mcp.tokens import mint_call_token, verify_call_token
from mabel_media.postcall import CallOutcome, finalize
from mabel_telnyx.client import FakeTelnyxClient
from mabel_worker import queue
from mabel_worker.jobs import morning_recap, send_notification

pytestmark = pytest.mark.asyncio

SIGNING_KEY = "an-end-to-end-signing-key-long-enough"
DID = "+12165550148"
CALLER = "+12165550100"
OWNER_PHONE = "+12165550111"

# 06:00 UTC is 02:00 in Cleveland.
CALL_START = datetime(2026, 10, 14, 6, 0, tzinfo=UTC)


class InMemoryBucket:
    """Stands in for Supabase Storage. Holds bytes in a dict.

    Not a no-op: `finalize` is meant to record a path when storage works and
    leave it null when it does not, and both branches matter.
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, path: str, data: bytes) -> None:
        self.objects[path] = data


@pytest.fixture
async def shop(app_engine: AsyncEngine, engine: AsyncEngine):
    """One plumbing shop, live, with an owner on call.

    Seeded as the schema owner because seeding through the app role is what the
    isolation suite tests; here we want a working shop, not a privilege check.
    """
    tenant_id = uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, business_name, trade, timezone, status, did_e164) "
                "VALUES (:id, 'Ruiz Plumbing', 'plumbing', 'America/New_York', 'active', :did)"
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
                  (tenant_id, version, is_live, greeting, business_hours,
                   services, service_area_zips)
                VALUES
                  (:t, 1, true, 'Thanks for calling Ruiz Plumbing.',
                   cast(:hours as jsonb), ARRAY['drain cleaning'], ARRAY['44107'])
                """
            ),
            {
                "t": tenant_id,
                "hours": json.dumps({"mon": {"open": "08:00", "close": "17:00"}}),
            },
        )
    yield tenant_id
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant_id})


class TestOneNight:
    """The whole thing, in order. Each test builds on the last."""

    async def test_the_dialed_number_resolves_to_the_shop(self, app_engine, shop):
        """Invariant 3, against a real database. Nothing the model says is
        involved: this happens before the socket opens."""
        async with admin_scope(reason="inbound call", engine=app_engine) as conn:
            resolved = await tenant_by_did(conn, DID)

        assert resolved is not None
        assert resolved["tenant_id"] == shop
        assert resolved["business_name"] == "Ruiz Plumbing"
        assert resolved["trade"] == "plumbing"

    async def test_the_call_token_carries_that_tenant_and_nothing_else(self, shop):
        token = verify_call_token(
            mint_call_token(shop, "call_e2e_1", key=SIGNING_KEY), key=SIGNING_KEY
        )
        assert token.tenant_id == shop
        assert token.call_id == "call_e2e_1"

    async def test_she_takes_the_details_and_escalates(self, app_engine, shop):
        """The MCP path end to end: real dispatcher, real handler, real
        transaction, real RLS."""
        token = verify_call_token(
            mint_call_token(shop, "call_e2e_1", key=SIGNING_KEY), key=SIGNING_KEY
        )

        area = await dispatch("get_service_area", {"zip": "44107"}, token=token, engine=app_engine)
        assert area.content["in_area"] is True

        result = await dispatch(
            "escalate_emergency",
            {
                "name": "Henderson",
                "phone": CALLER,
                "address": "100 Example Ave, Lakewood",
                "nature": "burst pipe in the basement",
                "caller_is_safe": True,
            },
            token=token,
            engine=app_engine,
        )

        assert result.content["escalated"] is True
        # Somebody was actually reachable, so she can say "right back".
        assert result.content["oncall_reached"] is True

        async with tenant_scope(shop, engine=app_engine) as conn:
            lead = await conn.execute(
                text(
                    "SELECT urgency, caller_name, callback_e164, escalated_at, value_cents "
                    "FROM leads WHERE caller_name = 'Henderson'"
                )
            )
            row = lead.mappings().one()
            assert row["urgency"] == "emergency"
            assert row["callback_e164"] == CALLER
            assert row["escalated_at"] is not None
            # Nothing on the call path writes money.
            assert row["value_cents"] is None

    async def test_the_owners_phone_gets_a_text(self, app_engine, shop):
        """The emergency SMS is queued inside the same transaction as the lead,
        then sent by the worker through the Telnyx double."""
        token = verify_call_token(
            mint_call_token(shop, "call_e2e_2", key=SIGNING_KEY), key=SIGNING_KEY
        )
        await dispatch(
            "escalate_emergency",
            {"name": "Henderson", "phone": CALLER, "nature": "burst pipe"},
            token=token,
            engine=app_engine,
        )

        telnyx = FakeTelnyxClient()
        job = queue.Job(
            id=1,
            tenant_id=shop,
            kind="send_notification",
            payload={},
            attempts=1,
            max_attempts=5,
            created_at=CALL_START,
        )

        import os

        os.environ["TELNYX_FROM_E164"] = DID
        try:
            await send_notification.run(job, app_engine, client=telnyx)
        finally:
            os.environ.pop("TELNYX_FROM_E164", None)

        assert telnyx.sent, "nobody's phone rang"
        assert telnyx.sent[0].to_e164 == OWNER_PHONE
        body = telnyx.bodies[0]
        assert body.startswith("EMERGENCY")
        assert "Henderson" in body
        assert "(216) 555-0100" in body
        # An emergency text with a dollar figure in it would be a quote.
        assert "$" not in body

        async with tenant_scope(shop, engine=app_engine) as conn:
            sent = await conn.execute(
                text("SELECT status, provider_ref FROM notifications WHERE kind = 'emergency'")
            )
            row = sent.mappings().one()
            assert row["status"] == "sent"
            assert row["provider_ref"]

    async def test_the_call_is_archived_with_a_transcript_and_a_cost(self, app_engine, shop):
        bucket = InMemoryBucket()
        outcome = CallOutcome(
            call_id="call_e2e_3",
            tenant_id=shop,
            timezone="America/New_York",
            trade="plumbing",
            from_e164=CALLER,
            to_e164=DID,
            started_at=CALL_START,
            ended_at=CALL_START + timedelta(minutes=3),
            turns=[
                {"role": "assistant", "text": "Thanks for calling Ruiz Plumbing."},
                {"role": "caller", "text": "My pipe burst in the basement."},
                {"role": "assistant", "text": "I'll get someone out to you."},
            ],
            tool_trace=[{"tool": "escalate_emergency", "ok": True, "mutating": True}],
            escalated=True,
            recording_bytes=b"fake-ulaw-audio",
        )

        archived = await finalize(outcome, storage=bucket, engine=app_engine)

        # Cost is integer cents from a duration and a published rate.
        assert archived.voice_cost_cents == 25
        assert isinstance(archived.voice_cost_cents, int)
        assert archived.outcome == "emergency"
        # She escalated a burst pipe, so the backstop agrees and nothing flags.
        assert archived.qa_flags == []
        assert archived.recording_path == f"{shop}/2026-10-14/call_e2e_3.ulaw"
        assert bucket.objects[archived.recording_path] == b"fake-ulaw-audio"

        async with tenant_scope(shop, engine=app_engine) as conn:
            stored = await conn.execute(
                text(
                    "SELECT c.duration_sec, c.voice_cost_cents, t.full_text "
                    "FROM calls c JOIN transcripts t ON t.call_id = c.id "
                    "WHERE c.xai_call_id = 'call_e2e_3'"
                )
            )
            row = stored.mappings().one()
            assert row["duration_sec"] == 180
            assert row["voice_cost_cents"] == 25
            assert "Mabel: Thanks for calling" in row["full_text"]
            assert "Caller: My pipe burst" in row["full_text"]

            usage = await conn.execute(
                text("SELECT voice_minutes, cost_cents FROM usage_daily WHERE day = :d"),
                {"d": CALL_START.date()},
            )
            counted = usage.mappings().one()
            assert float(counted["voice_minutes"]) == 3.0

    async def test_the_transcript_is_searchable(self, app_engine, shop):
        """02-PORTAL.md's "search for that guy who called about the water
        heater". Against the real to_tsvector index."""
        await finalize(
            CallOutcome(
                call_id="call_e2e_4",
                tenant_id=shop,
                timezone="America/New_York",
                trade="plumbing",
                from_e164=CALLER,
                to_e164=DID,
                started_at=CALL_START,
                ended_at=CALL_START + timedelta(minutes=2),
                turns=[{"role": "caller", "text": "the water heater is leaking"}],
            ),
            storage=None,
            engine=app_engine,
        )

        async with tenant_scope(shop, engine=app_engine) as conn:
            found = await conn.execute(
                text(
                    "SELECT count(*) FROM transcripts "
                    "WHERE to_tsvector('english', coalesce(full_text,'')) "
                    "@@ plainto_tsquery('english', 'water heater')"
                )
            )
            assert found.scalar_one() == 1

    async def test_the_7am_recap_is_composed_and_queued(self, app_engine, shop):
        """Phase 3's bar: Sam's own phone gets a useful 7am text."""
        token = verify_call_token(
            mint_call_token(shop, "call_e2e_5", key=SIGNING_KEY), key=SIGNING_KEY
        )
        await dispatch(
            "create_lead",
            {
                "name": "Henderson",
                "phone": CALLER,
                "job_type": "burst pipe",
                "urgency": "emergency",
            },
            token=token,
            engine=app_engine,
        )

        job = queue.Job(
            id=2,
            tenant_id=shop,
            kind="morning_recap",
            payload={},
            attempts=1,
            max_attempts=5,
            created_at=CALL_START,
        )
        await morning_recap.run(job, app_engine)

        async with tenant_scope(shop, engine=app_engine) as conn:
            recap = await conn.execute(
                text(
                    "SELECT to_address, body FROM notifications "
                    "WHERE kind = 'morning_recap' ORDER BY created_at DESC LIMIT 1"
                )
            )
            row = recap.mappings().one()
            assert row["to_address"] == OWNER_PHONE
            assert "Henderson" in row["body"]
            assert "Reply 1-3" in row["body"]
            # No figure in a recap unless the owner entered one.
            assert "$" not in row["body"]

            session = await conn.execute(
                text("SELECT context FROM sms_sessions WHERE phone_e164 = :p"),
                {"p": OWNER_PHONE},
            )
            context = session.scalar_one()
            assert context["last_list"], "he has nothing to reply 1 to"

    async def test_he_texts_back_won_and_the_money_lands(self, app_engine, shop):
        """The one place a dollar figure enters the system, end to end."""
        token = verify_call_token(
            mint_call_token(shop, "call_e2e_6", key=SIGNING_KEY), key=SIGNING_KEY
        )
        await dispatch(
            "create_lead",
            {
                "name": "Henderson",
                "phone": CALLER,
                "job_type": "burst pipe",
                "urgency": "emergency",
            },
            token=token,
            engine=app_engine,
        )

        async with admin_scope(reason="inbound sms", engine=app_engine) as conn:
            sender = await resolve_sender(conn, OWNER_PHONE)
        assert sender is not None
        assert sender.tenant_id == shop

        async with tenant_scope(shop, engine=app_engine) as conn:
            reply = await handle(conn, sender, "WON HENDERSON 3800", now=CALL_START)

        assert reply.action == "marked_won"
        assert "$3,800" in reply.body

        async with tenant_scope(shop, engine=app_engine) as conn:
            lead = await conn.execute(
                text(
                    "SELECT status, value_cents, won_at FROM leads "
                    "WHERE caller_name = 'Henderson' AND status = 'won'"
                )
            )
            row = lead.mappings().one()
            assert row["status"] == "won"
            # Integer cents, parsed by deterministic code from digits a human
            # typed. Never a float, never from a model.
            assert row["value_cents"] == 380_000
            assert isinstance(row["value_cents"], int)
            assert row["won_at"] is not None

            event = await conn.execute(
                text(
                    "SELECT body, actor_user_id FROM communication_events "
                    "WHERE kind = 'status_change' ORDER BY created_at DESC LIMIT 1"
                )
            )
            trail = event.mappings().one()
            assert "Marked won by SMS" in trail["body"]
            assert trail["actor_user_id"] == sender.user_id


class TestTheFailClosedPaths:
    """What happens when the accounts that do not exist are asked for.

    None of these should look like success, and none should lose data.
    """

    async def test_no_storage_still_archives_the_call(self, app_engine, shop):
        """docs/BLOCKED.md #2. A transcript with no audio is a bad day; no row
        at all is a call that never happened."""
        archived = await finalize(
            CallOutcome(
                call_id="call_e2e_nostorage",
                tenant_id=shop,
                timezone="America/New_York",
                trade="plumbing",
                from_e164=CALLER,
                to_e164=DID,
                started_at=CALL_START,
                ended_at=CALL_START + timedelta(minutes=1),
                turns=[{"role": "caller", "text": "hello"}],
                recording_bytes=b"audio-that-cannot-be-stored",
            ),
            storage=None,
            engine=app_engine,
        )
        assert archived.recording_path is None

        async with tenant_scope(shop, engine=app_engine) as conn:
            found = await conn.execute(
                text(
                    "SELECT recording_path, archived_at FROM calls "
                    "WHERE xai_call_id = 'call_e2e_nostorage'"
                )
            )
            row = found.mappings().one()
            assert row["recording_path"] is None
            assert row["archived_at"] is not None

    async def test_no_telnyx_records_the_notification_as_failed(self, app_engine, shop):
        """Never as sent. A recap the owner never received but that we marked
        delivered removes the only signal anybody would act on."""
        token = verify_call_token(
            mint_call_token(shop, "call_e2e_nosms", key=SIGNING_KEY), key=SIGNING_KEY
        )
        await dispatch(
            "escalate_emergency",
            {"name": "Nobody", "phone": CALLER, "nature": "burst pipe"},
            token=token,
            engine=app_engine,
        )

        job = queue.Job(
            id=3,
            tenant_id=shop,
            kind="send_notification",
            payload={},
            attempts=1,
            max_attempts=5,
            created_at=CALL_START,
        )
        # client=None and no TELNYX_FROM_E164: the unconfigured state.
        await send_notification.run(job, app_engine, client=None)

        async with tenant_scope(shop, engine=app_engine) as conn:
            found = await conn.execute(
                text(
                    "SELECT status, error FROM notifications "
                    "WHERE kind = 'emergency' ORDER BY created_at DESC LIMIT 1"
                )
            )
            row = found.mappings().one()
            assert row["status"] == "failed"
            assert "BLOCKED.md" in row["error"]

    async def test_an_emergency_with_nobody_on_call_is_reported_honestly(
        self, app_engine, engine, shop
    ):
        """She must not imply a truck is moving."""
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE users SET phone_e164 = NULL WHERE tenant_id = :t"),
                {"t": shop},
            )

        token = verify_call_token(
            mint_call_token(shop, "call_e2e_nooncall", key=SIGNING_KEY), key=SIGNING_KEY
        )
        result = await dispatch(
            "escalate_emergency",
            {"name": "Henderson", "phone": CALLER, "nature": "burst pipe"},
            token=token,
            engine=app_engine,
        )

        assert result.content["escalated"] is True
        assert result.content["oncall_reached"] is False

        # The lead still exists. The office manager can find it in the morning.
        async with tenant_scope(shop, engine=app_engine) as conn:
            found = await conn.execute(
                text("SELECT count(*) FROM leads WHERE urgency = 'emergency'")
            )
            assert found.scalar_one() >= 1


class TestNoMoneyEverReachesTheModel:
    """Invariant 4, checked over the real path rather than a fake repo."""

    async def test_job_history_carries_no_value(self, app_engine, engine, shop):
        async with engine.begin() as conn:
            contact = await conn.execute(
                text(
                    "INSERT INTO contacts (tenant_id, display_name, primary_phone, phones) "
                    "VALUES (:t, 'Henderson', :p, ARRAY[:p]) RETURNING id"
                ),
                {"t": shop, "p": CALLER},
            )
            contact_id = contact.scalar_one()
            await conn.execute(
                text(
                    "INSERT INTO leads (tenant_id, contact_id, caller_name, job_type, "
                    "status, value_cents, won_at) "
                    "VALUES (:t, :c, 'Henderson', 'water heater', 'won', 380000, now())"
                ),
                {"t": shop, "c": contact_id},
            )

        token = verify_call_token(
            mint_call_token(shop, "call_e2e_history", key=SIGNING_KEY), key=SIGNING_KEY
        )
        result = await dispatch(
            "get_job_history", {"phone": CALLER}, token=token, engine=app_engine
        )

        blob = json.dumps(result.content)
        assert result.content["found"] is True
        assert "380000" not in blob
        assert "3800" not in blob
        assert "value" not in blob

    async def test_lookup_customer_returns_a_null_open_balance(self, app_engine, engine, shop):
        """03-VOICE.md puts it in the contract; 01-SCHEMA.sql has nothing to
        compute it from. BLOCKED.md S5."""
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO contacts (tenant_id, display_name, primary_phone, phones) "
                    "VALUES (:t, 'Henderson', :p, ARRAY[:p])"
                ),
                {"t": shop, "p": CALLER},
            )

        token = verify_call_token(
            mint_call_token(shop, "call_e2e_lookup", key=SIGNING_KEY), key=SIGNING_KEY
        )
        result = await dispatch(
            "lookup_customer", {"phone": CALLER}, token=token, engine=app_engine
        )
        assert result.content["found"] is True
        assert result.content["open_balance"] is None


class TestASecondShopSeesNoneOfIt:
    """The whole night above happened to one tenant. A second one, on the same
    database, through the same pool, sees nothing."""

    async def test_a_second_tenant_is_untouched(self, app_engine, engine, shop):
        other = uuid4()
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, business_name, trade, status, did_e164) "
                    "VALUES (:id, 'Delgado HVAC', 'hvac', 'active', '+12165550199')"
                ),
                {"id": other},
            )

        try:
            token = verify_call_token(
                mint_call_token(shop, "call_e2e_iso", key=SIGNING_KEY), key=SIGNING_KEY
            )
            await dispatch(
                "create_lead",
                {
                    "name": "Henderson",
                    "phone": CALLER,
                    "job_type": "burst pipe",
                    "urgency": "emergency",
                },
                token=token,
                engine=app_engine,
            )

            async with tenant_scope(other, engine=app_engine) as conn:
                for table in ("leads", "calls", "contacts", "notifications"):
                    count = await conn.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
                    assert count.scalar_one() == 0, f"{table} leaked across tenants"
        finally:
            async with engine.begin() as conn:
                await conn.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": other})
