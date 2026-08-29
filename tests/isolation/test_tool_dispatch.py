"""Tool dispatch against a real database, with RLS doing its job.

The unit tests prove the handlers make the right decisions. These prove the
decisions land in the right tenant — and that a tool call carrying another
tenant's id in its arguments gets that tenant's data exactly never.

This is the end of the chain that starts at a dialed number: DID -> tenant ->
token -> SET LOCAL -> policy. If any link is wrong, one of these fails.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabel_mcp.registry import dispatch
from mabel_mcp.tokens import mint_call_token, verify_call_token

from .conftest import rows_visible

pytestmark = pytest.mark.asyncio

KEY = "a-test-signing-key-long-enough-to-be-accepted"


def token_for(tenant_id: UUID, call_id: str = "call_abc"):
    return verify_call_token(mint_call_token(tenant_id, call_id, key=KEY), key=KEY)


class TestAToolCallLandsInOneTenant:
    async def test_a_lead_created_on_one_call_is_invisible_to_the_other_tenant(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        alpha, beta = two_tenants

        result = await dispatch(
            "create_lead",
            {
                "name": "Pat Example",
                "phone": "216-555-0177",
                "job_type": "burst pipe",
                "urgency": "emergency",
            },
            token=token_for(alpha),
            engine=app_engine,
        )
        assert result.content["created"] is True

        from mabel_db.tenant import tenant_scope

        async with tenant_scope(alpha, engine=app_engine) as conn:
            found = await conn.execute(
                text("SELECT count(*) FROM leads WHERE caller_name = 'Pat Example'")
            )
            assert found.scalar_one() == 1

        async with tenant_scope(beta, engine=app_engine) as conn:
            found = await conn.execute(
                text("SELECT count(*) FROM leads WHERE caller_name = 'Pat Example'")
            )
            assert found.scalar_one() == 0

    async def test_a_tenant_id_in_the_arguments_is_not_honoured(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        """The attack the whole design is against: the model passes another
        tenant's id. The token decides, so the row lands in alpha regardless."""
        alpha, beta = two_tenants

        await dispatch(
            "create_lead",
            {
                "name": "Smuggled",
                "phone": "216-555-0188",
                "job_type": "x",
                "urgency": "routine",
                "tenant_id": str(beta),
                "account_id": str(beta),
            },
            token=token_for(alpha),
            engine=app_engine,
        )

        from mabel_db.tenant import tenant_scope

        async with tenant_scope(beta, engine=app_engine) as conn:
            found = await conn.execute(
                text("SELECT count(*) FROM leads WHERE caller_name = 'Smuggled'")
            )
            assert found.scalar_one() == 0

        async with tenant_scope(alpha, engine=app_engine) as conn:
            found = await conn.execute(
                text("SELECT count(*) FROM leads WHERE caller_name = 'Smuggled'")
            )
            assert found.scalar_one() == 1

    async def test_lookup_customer_cannot_see_the_other_tenants_contacts(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        """Both tenants have a contact on +12165550001. Each call must find
        only its own."""
        alpha, beta = two_tenants

        for tenant in (alpha, beta):
            result = await dispatch(
                "lookup_customer",
                {"phone": "+12165550001"},
                token=token_for(tenant),
                engine=app_engine,
            )
            assert result.content["found"] is True
            expected = "Ruiz Plumbing caller" if tenant == alpha else "Delgado HVAC caller"
            assert result.content["name"] == expected


class TestTheTransactionIsAtomic:
    async def test_an_emergency_writes_the_lead_and_the_alert_together(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        """A text about a lead that was rolled back is a contractor woken at
        3am for a record he cannot find."""
        alpha, _beta = two_tenants
        from mabel_db.tenant import tenant_scope

        # Give the tenant somebody to wake.
        async with tenant_scope(alpha, engine=app_engine) as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (tenant_id, email, full_name, phone_e164, role, "
                    "notify_emergencies) VALUES (:t, 'ray@example.com', 'Ray', "
                    "'+12165550123', 'owner', true)"
                ),
                {"t": alpha},
            )

        result = await dispatch(
            "escalate_emergency",
            {"name": "Pat", "phone": "216-555-0199", "nature": "burst pipe"},
            token=token_for(alpha),
            engine=app_engine,
        )
        assert result.content["escalated"] is True
        assert result.content["oncall_reached"] is True

        async with tenant_scope(alpha, engine=app_engine) as conn:
            leads = await conn.execute(
                text("SELECT id FROM leads WHERE urgency = 'emergency' AND caller_name = 'Pat'")
            )
            lead_id = leads.scalar_one()
            queued = await conn.execute(
                text(
                    "SELECT count(*) FROM notifications "
                    "WHERE kind = 'emergency' AND lead_id = :lead AND status = 'queued'"
                ),
                {"lead": lead_id},
            )
            assert queued.scalar_one() == 1

    async def test_nobody_on_call_is_reported_rather_than_silently_dropped(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        """The tenant has no user with a phone number. The lead is still
        written and Mabel is told nobody was reached, so she does not imply a
        truck is moving."""
        alpha, _beta = two_tenants
        result = await dispatch(
            "escalate_emergency",
            {"name": "Unreachable", "phone": "216-555-0166", "nature": "flooding"},
            token=token_for(alpha),
            engine=app_engine,
        )
        assert result.content["escalated"] is True
        assert result.content["oncall_reached"] is False

        from mabel_db.tenant import tenant_scope

        async with tenant_scope(alpha, engine=app_engine) as conn:
            found = await conn.execute(
                text("SELECT count(*) FROM leads WHERE caller_name = 'Unreachable'")
            )
            assert found.scalar_one() == 1


class TestKnowledgeStaysWithItsTenant:
    async def test_one_tenants_answers_are_not_offered_to_another(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        alpha, beta = two_tenants
        from mabel_db.tenant import tenant_scope

        async with tenant_scope(alpha, engine=app_engine) as conn:
            await conn.execute(
                text(
                    "INSERT INTO knowledge_items (tenant_id, question, answer) "
                    "VALUES (:t, 'Do you do drywall repair?', "
                    "'Yes, as part of a painting job.')"
                ),
                {"t": alpha},
            )

        mine = await dispatch(
            "answer_question",
            {"question": "do you do drywall repair"},
            token=token_for(alpha),
            engine=app_engine,
        )
        assert mine.content["found"] is True

        theirs = await dispatch(
            "answer_question",
            {"question": "do you do drywall repair"},
            token=token_for(beta),
            engine=app_engine,
        )
        assert theirs.content["found"] is False


class TestNoMoneyReachesTheModel:
    async def test_job_history_over_a_real_database_carries_no_value(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        """The seeded lead is worth 380000 cents. The end-to-end path must not
        surface it, whatever the query layer selects."""
        import json

        alpha, _beta = two_tenants
        from mabel_db.tenant import tenant_scope

        async with tenant_scope(alpha, engine=app_engine) as conn:
            contact = await conn.execute(
                text("SELECT id FROM contacts WHERE primary_phone = '+12165550001'")
            )
            contact_id = contact.scalar_one()
            await conn.execute(
                text("UPDATE leads SET contact_id = :c WHERE contact_id IS NULL"),
                {"c": contact_id},
            )

        result = await dispatch(
            "get_job_history",
            {"phone": "+12165550001"},
            token=token_for(alpha),
            engine=app_engine,
        )
        blob = json.dumps(result.content)
        assert "380000" not in blob
        assert "value" not in blob


class TestAToolCallLeavesNoContextBehind:
    async def test_the_connection_is_clean_afterwards(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        """The pool holds two connections with no overflow, so the connection
        this call used is one a later request will get back."""
        alpha, _beta = two_tenants
        await dispatch(
            "answer_question", {"question": "x"}, token=token_for(alpha), engine=app_engine
        )
        async with app_engine.connect() as conn, conn.begin():
            assert await rows_visible(conn, "leads") == 0
