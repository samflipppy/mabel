"""Resolving a dialed number to a tenant, before any tenant context exists.

Invariant 3 is the load-bearing one: the tenant comes from the dialed number,
server-side, before the socket opens. If this lookup does not work, no call is
ever routed. If it works too well — if it can be used to read anything beyond
routing facts — then a stranger dialling numbers can enumerate customers.

These need the SECURITY DEFINER function from migration 0003, which the
conftest applies alongside the schema.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabel_db.queries.config import tenant_by_did
from mabel_db.tenant import admin_scope, tenant_scope

from .conftest import rows_visible

pytestmark = pytest.mark.asyncio

ALPHA_DID = "+12165550148"
BETA_DID = "+12165550199"


class TestTheLookupWorksAtAll:
    async def test_a_known_number_resolves(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        alpha, _beta = two_tenants
        async with admin_scope(reason="resolve inbound DID", engine=app_engine) as conn:
            found = await tenant_by_did(conn, ALPHA_DID)
        assert found is not None
        assert found["tenant_id"] == alpha
        assert found["business_name"] == "Ruiz Plumbing"

    async def test_each_number_resolves_to_its_own_tenant(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        alpha, beta = two_tenants
        async with admin_scope(reason="resolve inbound DID", engine=app_engine) as conn:
            assert (await tenant_by_did(conn, ALPHA_DID))["tenant_id"] == alpha  # type: ignore[index]
            assert (await tenant_by_did(conn, BETA_DID))["tenant_id"] == beta  # type: ignore[index]

    async def test_an_unknown_number_resolves_to_nothing(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        """A call to a number we do not know must fall through to the carrier's
        voicemail, not to somebody else's Mabel."""
        async with admin_scope(reason="resolve inbound DID", engine=app_engine) as conn:
            assert await tenant_by_did(conn, "+12165550000") is None

    async def test_a_plain_select_on_tenants_would_not_have_worked(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        """The reason migration 0003 exists, stated as a test.

        `tenants` has RLS forced. With no tenant context — which is exactly the
        situation when resolving a DID — a direct SELECT returns nothing,
        however correct the SQL looks.
        """
        async with admin_scope(reason="demonstrating the gap", engine=app_engine) as conn:
            result = await conn.execute(
                text("SELECT count(*) FROM tenants WHERE did_e164 = :did"), {"did": ALPHA_DID}
            )
            assert result.scalar_one() == 0


class TestTheFunctionIsNarrow:
    async def test_it_returns_routing_facts_and_nothing_else(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        """Anyone who can dial a number can reach this. It must give up nothing
        beyond what is needed to route the call."""
        async with admin_scope(reason="check the surface", engine=app_engine) as conn:
            found = await tenant_by_did(conn, ALPHA_DID)
        assert found is not None
        assert set(found) == {
            "tenant_id",
            "location_id",
            "business_name",
            "trade",
            "timezone",
            "status",
            "xai_agent_id",
        }

    async def test_it_does_not_open_a_door_to_the_rest_of_the_schema(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        """SECURITY DEFINER applies to the function body, not to the caller.
        After calling it, the connection is as constrained as it was before."""
        async with admin_scope(reason="check the blast radius", engine=app_engine) as conn:
            await tenant_by_did(conn, ALPHA_DID)
            for table in ("leads", "calls", "contacts", "transcripts"):
                assert await rows_visible(conn, table) == 0

    async def test_its_search_path_is_pinned(self, engine: AsyncEngine):
        """A SECURITY DEFINER function without a pinned search_path is the
        classic Postgres escalation: the caller points `tenants` at a table
        they control and the function reads that instead."""
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT proconfig FROM pg_proc WHERE proname = 'resolve_tenant_by_did'")
            )
            config = result.scalar_one()
            assert config is not None, "resolve_tenant_by_did has no search_path pinned"
            assert any(entry.startswith("search_path=") for entry in config)

    async def test_it_is_not_executable_by_the_world(self, engine: AsyncEngine):
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT has_function_privilege('public', "
                    "'resolve_tenant_by_did(text)', 'EXECUTE')"
                )
            )
            assert result.scalar_one() is False


class TestTheEmptySettingCase:
    """The other bug this branch fixed. `''::uuid` raises rather than returning
    NULL, so a policy written with a bare cast errors instead of failing
    closed, and an error in the wrong place gets swallowed upstream."""

    async def test_an_empty_tenant_setting_returns_nothing_rather_than_erroring(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        async with app_engine.connect() as conn, conn.begin():
            await conn.execute(text("SET LOCAL app.tenant_id = ''"))
            # The assertion is as much that this does not raise as that it is 0.
            assert await rows_visible(conn, "leads") == 0
            assert await rows_visible(conn, "tenants") == 0

    async def test_admin_scope_which_sets_it_empty_is_usable(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        async with admin_scope(reason="the empty-setting path", engine=app_engine) as conn:
            assert await rows_visible(conn, "leads") == 0

    async def test_a_real_tenant_still_sees_its_own_rows(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        # The nullif() must not have broken the case that matters.
        alpha, _beta = two_tenants
        async with tenant_scope(alpha, engine=app_engine) as conn:
            assert await rows_visible(conn, "leads") == 1
