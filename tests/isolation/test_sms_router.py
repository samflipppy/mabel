"""The SMS command router, against a real database.

The grammar is unit-tested and pure. What needs a database is resolution: which
tenant a phone number belongs to, which lead "RUIZ" is, and — the point of this
file — that a command from one owner cannot touch another tenant's leads.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabel_api.sms_router import Sender, handle, resolve_sender
from mabel_db.tenant import admin_scope, tenant_scope

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
ALPHA_PHONE = "+12165550111"
BETA_PHONE = "+12165550222"


@pytest.fixture
async def owners(app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]):
    """An owner on each tenant, each with a lead named the same thing.

    Same name on both sides on purpose: it is the arrangement in which a
    tenant-resolution bug actually does damage.
    """
    alpha, beta = two_tenants
    for tenant_id, phone, email in (
        (alpha, ALPHA_PHONE, "ray@ruiz.example"),
        (beta, BETA_PHONE, "dee@delgado.example"),
    ):
        async with tenant_scope(tenant_id, engine=app_engine) as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (tenant_id, email, full_name, phone_e164, role, "
                    "notify_recap, notify_emergencies) "
                    "VALUES (:t, :e, 'Owner', :p, 'owner', true, true)"
                ),
                {"t": tenant_id, "e": email, "p": phone},
            )
            await conn.execute(
                text(
                    "INSERT INTO leads (tenant_id, caller_name, job_type, urgency, status) "
                    "VALUES (:t, 'Henderson', 'water heater', 'routine', 'new')"
                ),
                {"t": tenant_id},
            )
    return alpha, beta


async def sender_for(app_engine: AsyncEngine, phone: str) -> Sender:
    async with admin_scope(reason="resolve an SMS sender", engine=app_engine) as conn:
        found = await resolve_sender(conn, phone)
    assert found is not None
    return found


class TestSenderResolution:
    async def test_a_known_number_resolves_to_its_tenant(self, app_engine, owners):
        alpha, _beta = owners
        assert (await sender_for(app_engine, ALPHA_PHONE)).tenant_id == alpha

    async def test_an_unknown_number_resolves_to_nothing(self, app_engine, owners):
        async with admin_scope(reason="resolve", engine=app_engine) as conn:
            assert await resolve_sender(conn, "+12165559999") is None

    async def test_a_number_on_two_tenants_refuses_to_guess(self, app_engine, owners):
        """An office manager who works for two contractors is a real thing, and
        guessing which business he meant files the lead in the wrong one."""
        alpha, beta = owners
        shared = "+12165550333"
        for tenant_id, email in ((alpha, "shared@a.example"), (beta, "shared@b.example")):
            async with tenant_scope(tenant_id, engine=app_engine) as conn:
                await conn.execute(
                    text(
                        "INSERT INTO users (tenant_id, email, phone_e164, role) "
                        "VALUES (:t, :e, :p, 'office')"
                    ),
                    {"t": tenant_id, "e": email, "p": shared},
                )

        async with admin_scope(reason="resolve", engine=app_engine) as conn:
            assert await resolve_sender(conn, shared) is None


class TestCommandsStayInTheirTenant:
    async def test_marking_won_only_touches_the_senders_lead(self, app_engine, owners):
        """Both tenants have a lead called Henderson. Alpha's owner texting WON
        HENDERSON must not close Beta's."""
        alpha, beta = owners
        sender = await sender_for(app_engine, ALPHA_PHONE)

        async with tenant_scope(alpha, engine=app_engine) as conn:
            reply = await handle(conn, sender, "WON HENDERSON 3800", now=NOW)
        assert reply.action == "marked_won"

        async with tenant_scope(alpha, engine=app_engine) as conn:
            mine = await conn.execute(
                text("SELECT status, value_cents FROM leads WHERE caller_name = 'Henderson'")
            )
            row = mine.mappings().one()
            assert row["status"] == "won"
            assert row["value_cents"] == 380_000

        async with tenant_scope(beta, engine=app_engine) as conn:
            theirs = await conn.execute(
                text("SELECT status, value_cents FROM leads WHERE caller_name = 'Henderson'")
            )
            row = theirs.mappings().one()
            assert row["status"] == "new"
            assert row["value_cents"] is None

    async def test_the_value_is_written_as_integer_cents(self, app_engine, owners):
        alpha, _beta = owners
        sender = await sender_for(app_engine, ALPHA_PHONE)
        async with tenant_scope(alpha, engine=app_engine) as conn:
            await handle(conn, sender, "WON HENDERSON 3800", now=NOW)
            value = await conn.execute(
                text("SELECT value_cents FROM leads WHERE caller_name = 'Henderson'")
            )
            stored = value.scalar_one()
            assert isinstance(stored, int)
            assert stored == 380_000

    async def test_marking_won_without_a_figure_leaves_the_value_alone(self, app_engine, owners):
        alpha, _beta = owners
        sender = await sender_for(app_engine, ALPHA_PHONE)
        async with tenant_scope(alpha, engine=app_engine) as conn:
            reply = await handle(conn, sender, "WON HENDERSON", now=NOW)
            assert "value" in reply.body.lower()
            value = await conn.execute(
                text("SELECT status, value_cents FROM leads WHERE caller_name = 'Henderson'")
            )
            row = value.mappings().one()
            assert row["status"] == "won"
            assert row["value_cents"] is None

    async def test_marking_lost_records_the_reason(self, app_engine, owners):
        alpha, _beta = owners
        sender = await sender_for(app_engine, ALPHA_PHONE)
        async with tenant_scope(alpha, engine=app_engine) as conn:
            await handle(conn, sender, "LOST HENDERSON went with someone else", now=NOW)
            found = await conn.execute(
                text("SELECT status, lost_reason FROM leads WHERE caller_name = 'Henderson'")
            )
            row = found.mappings().one()
            assert row["status"] == "lost"
            assert "someone else" in row["lost_reason"]

    async def test_a_name_matching_nothing_is_refused_not_guessed(self, app_engine, owners):
        """Marking the wrong job won puts a wrong number on the monthly
        report."""
        alpha, _beta = owners
        sender = await sender_for(app_engine, ALPHA_PHONE)
        async with tenant_scope(alpha, engine=app_engine) as conn:
            reply = await handle(conn, sender, "WON NOBODY 500", now=NOW)
            assert reply.action is None
            assert "No open lead" in reply.body

            untouched = await conn.execute(text("SELECT count(*) FROM leads WHERE status = 'won'"))
            assert untouched.scalar_one() == 0


class TestTheThreadRecordsWhatHappened:
    async def test_a_won_command_lands_in_the_thread(self, app_engine, owners):
        """The office manager looking at a lead marked won needs to see who did
        it and how."""
        alpha, _beta = owners
        sender = await sender_for(app_engine, ALPHA_PHONE)
        async with tenant_scope(alpha, engine=app_engine) as conn:
            await handle(conn, sender, "WON HENDERSON 3800", now=NOW)
            events = await conn.execute(
                text(
                    "SELECT body, actor_user_id, kind FROM communication_events "
                    "WHERE kind = 'status_change'"
                )
            )
            row = events.mappings().one()
            assert "Marked won by SMS" in row["body"]
            assert row["actor_user_id"] == sender.user_id


class TestFollowupsAndExpansion:
    async def test_fu_lists_what_is_waiting_and_remembers_it(self, app_engine, owners):
        alpha, _beta = owners
        sender = await sender_for(app_engine, ALPHA_PHONE)

        async with tenant_scope(alpha, engine=app_engine) as conn:
            reply = await handle(conn, sender, "FU", now=NOW)
            assert "Henderson" in reply.body

            stored = await conn.execute(
                text("SELECT context FROM sms_sessions WHERE phone_e164 = :p"),
                {"p": ALPHA_PHONE},
            )
            context = stored.scalar_one()
            assert context["last_list"][0]["name"] == "Henderson"

    async def test_a_following_digit_expands_that_item(self, app_engine, owners):
        alpha, _beta = owners
        sender = await sender_for(app_engine, ALPHA_PHONE)
        async with tenant_scope(alpha, engine=app_engine) as conn:
            await handle(conn, sender, "FU", now=NOW)
            reply = await handle(conn, sender, "1", now=NOW)
            assert "Henderson" in reply.body

    async def test_a_digit_with_no_list_says_so(self, app_engine, owners):
        alpha, _beta = owners
        sender = await sender_for(app_engine, ALPHA_PHONE)
        async with tenant_scope(alpha, engine=app_engine) as conn:
            reply = await handle(conn, sender, "2", now=NOW)
            assert "expired" in reply.body

    async def test_an_index_past_the_end_is_answered_not_crashed(self, app_engine, owners):
        alpha, _beta = owners
        sender = await sender_for(app_engine, ALPHA_PHONE)
        async with tenant_scope(alpha, engine=app_engine) as conn:
            await handle(conn, sender, "FU", now=NOW)
            reply = await handle(conn, sender, "9", now=NOW)
            assert "only 1" in reply.body


class TestStop:
    async def test_it_turns_every_notification_off(self, app_engine, owners):
        alpha, _beta = owners
        sender = await sender_for(app_engine, ALPHA_PHONE)
        async with tenant_scope(alpha, engine=app_engine) as conn:
            reply = await handle(conn, sender, "STOP", now=NOW)
            assert reply.action == "opted_out"

            prefs = await conn.execute(
                text("SELECT notify_recap, notify_emergencies FROM users WHERE id = :id"),
                {"id": sender.user_id},
            )
            row = prefs.mappings().one()
            assert row["notify_recap"] is False
            assert row["notify_emergencies"] is False

    async def test_it_does_not_affect_the_other_tenant(self, app_engine, owners):
        alpha, beta = owners
        sender = await sender_for(app_engine, ALPHA_PHONE)
        async with tenant_scope(alpha, engine=app_engine) as conn:
            await handle(conn, sender, "STOP", now=NOW)

        async with tenant_scope(beta, engine=app_engine) as conn:
            prefs = await conn.execute(text("SELECT notify_recap FROM users"))
            assert prefs.scalar_one() is True


class TestRecall:
    async def test_an_unrecognised_question_searches_the_thread(self, app_engine, owners):
        alpha, _beta = owners
        sender = await sender_for(app_engine, ALPHA_PHONE)

        async with tenant_scope(alpha, engine=app_engine) as conn:
            await conn.execute(
                text(
                    "INSERT INTO communication_events "
                    "(tenant_id, kind, direction, occurred_at, body) "
                    "VALUES (:t, 'note', 'inbound', now(), "
                    "'Caller asked about a water heater replacement')"
                ),
                {"t": alpha},
            )
            reply = await handle(conn, sender, "anything about a water heater", now=NOW)
            # No model is wired up, so the grounded fallback is returned rather
            # than anything invented. That is correct behaviour, not a stub.
            assert "$" not in reply.body

    async def test_nothing_found_says_so_rather_than_inventing(self, app_engine, owners):
        alpha, _beta = owners
        sender = await sender_for(app_engine, ALPHA_PHONE)
        async with tenant_scope(alpha, engine=app_engine) as conn:
            reply = await handle(conn, sender, "what about the boiler in Solon", now=NOW)
            assert "couldn't find" in reply.body

    async def test_recall_cannot_reach_the_other_tenants_thread(self, app_engine, owners):
        alpha, beta = owners
        async with tenant_scope(beta, engine=app_engine) as conn:
            await conn.execute(
                text(
                    "INSERT INTO communication_events "
                    "(tenant_id, kind, direction, occurred_at, body) "
                    "VALUES (:t, 'note', 'inbound', now(), "
                    "'Delgado secret about a water heater')"
                ),
                {"t": beta},
            )

        sender = await sender_for(app_engine, ALPHA_PHONE)
        async with tenant_scope(alpha, engine=app_engine) as conn:
            reply = await handle(conn, sender, "water heater", now=NOW)
            assert "Delgado" not in reply.body
            assert "secret" not in reply.body
