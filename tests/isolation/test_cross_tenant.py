"""Cross-tenant isolation. The database refuses, not our code.

Every test here asks the same question in a different way: if the application
forgets to filter by tenant, or is tricked into filtering by the wrong one,
what does Postgres hand back? The answer must always be *nothing*.

CI never skips this suite. A green run without a database is not a pass — the
conftest skips loudly and the CI job supplies a Postgres service so it cannot.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from mabel_db.tenant import TenantScopeError, admin_scope, current_tenant, tenant_scope

from .conftest import rows_visible

pytestmark = pytest.mark.asyncio

TENANT_SCOPED_TABLES = (
    "locations",
    "users",
    "agent_configs",
    "knowledge_items",
    "oncall_schedules",
    "contacts",
    "communication_events",
    "calls",
    "transcripts",
    "leads",
    "appointments",
    "notifications",
    "sms_sessions",
    "integrations",
    "integration_events",
    "subscriptions",
    "usage_daily",
    "monthly_reports",
)


class TestFailsClosed:
    """With no tenant set, policies compare against NULL and match zero rows.
    This is the direction we want to fail in: a query that forgets its tenant
    returns nothing, never everything."""

    @pytest.mark.parametrize("table", TENANT_SCOPED_TABLES)
    async def test_no_tenant_context_sees_nothing(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID], table: str
    ):
        async with app_engine.connect() as conn, conn.begin():
            assert await rows_visible(conn, table) == 0

    async def test_an_unset_setting_reads_as_null_not_an_error(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        # The `true` in current_setting(..., true) is what makes this a NULL
        # rather than a raised exception. An exception here would get caught
        # somewhere upstream and turned into a 500 that looks like a bug
        # instead of a policy doing its job.
        async with app_engine.connect() as conn, conn.begin():
            assert await current_tenant(conn) is None

    async def test_a_garbage_tenant_setting_still_sees_nothing(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        alpha, _beta = two_tenants
        async with app_engine.connect() as conn, conn.begin():
            await conn.execute(text("SET LOCAL app.tenant_id = ''"))
            assert await rows_visible(conn, "leads") == 0
        del alpha


class TestReadIsolation:
    @pytest.mark.parametrize("table", ["leads", "calls", "contacts"])
    async def test_a_tenant_sees_only_its_own_rows(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID], table: str
    ):
        alpha, beta = two_tenants
        async with tenant_scope(alpha, engine=app_engine) as conn:
            assert await rows_visible(conn, table) == 1
            result = await conn.execute(text(f"SELECT DISTINCT tenant_id FROM {table}"))
            assert [row[0] for row in result] == [alpha]

        async with tenant_scope(beta, engine=app_engine) as conn:
            result = await conn.execute(text(f"SELECT DISTINCT tenant_id FROM {table}"))
            assert [row[0] for row in result] == [beta]

    async def test_naming_the_other_tenant_explicitly_still_returns_nothing(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        """The attack this models: a tool argument or a query parameter that
        carries someone else's tenant id. The policy ANDs with whatever the
        query asks for, so asking for beta while scoped to alpha yields
        nothing at all."""
        alpha, beta = two_tenants
        async with tenant_scope(alpha, engine=app_engine) as conn:
            result = await conn.execute(
                text("SELECT count(*) FROM leads WHERE tenant_id = :other"), {"other": beta}
            )
            assert result.scalar_one() == 0

    async def test_a_tenant_sees_only_its_own_row_in_tenants(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        alpha, beta = two_tenants
        async with tenant_scope(alpha, engine=app_engine) as conn:
            result = await conn.execute(text("SELECT id FROM tenants"))
            assert [row[0] for row in result] == [alpha]
        del beta

    async def test_a_join_cannot_be_used_to_reach_across(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        """Policies apply per table, so a join is filtered on both sides.
        There is no arrangement of joins that reaches another tenant's rows."""
        alpha, _beta = two_tenants
        async with tenant_scope(alpha, engine=app_engine) as conn:
            result = await conn.execute(
                text("SELECT count(*) FROM leads l JOIN contacts c ON c.tenant_id <> l.tenant_id")
            )
            assert result.scalar_one() == 0


class TestWriteIsolation:
    async def test_cannot_insert_a_row_belonging_to_another_tenant(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        """WITH CHECK is what stops this. USING alone would let a write land
        somewhere it could never be read back from."""
        alpha, beta = two_tenants
        with pytest.raises(DBAPIError) as exc:
            async with tenant_scope(alpha, engine=app_engine) as conn:
                await conn.execute(
                    text("INSERT INTO leads (tenant_id, caller_name) VALUES (:t, 'smuggled')"),
                    {"t": beta},
                )
        assert "row-level security" in str(exc.value).lower()

    async def test_cannot_move_a_row_to_another_tenant(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        alpha, beta = two_tenants
        with pytest.raises(DBAPIError) as exc:
            async with tenant_scope(alpha, engine=app_engine) as conn:
                await conn.execute(text("UPDATE leads SET tenant_id = :other"), {"other": beta})
        assert "row-level security" in str(exc.value).lower()

    async def test_an_update_without_a_where_clause_touches_only_our_rows(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        """The careless-update case. `UPDATE leads SET status = 'lost'` with no
        WHERE is a bug, but it must be a bug that only harms one tenant."""
        alpha, beta = two_tenants
        async with tenant_scope(alpha, engine=app_engine) as conn:
            await conn.execute(text("UPDATE leads SET status = 'lost'"))

        async with tenant_scope(beta, engine=app_engine) as conn:
            result = await conn.execute(text("SELECT status FROM leads"))
            assert [row[0] for row in result] == ["new"]

    async def test_a_delete_without_a_where_clause_is_equally_contained(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        alpha, beta = two_tenants
        async with tenant_scope(alpha, engine=app_engine) as conn:
            await conn.execute(text("DELETE FROM leads"))

        async with tenant_scope(beta, engine=app_engine) as conn:
            assert await rows_visible(conn, "leads") == 1


class TestSetLocalDoesNotLeak:
    """Invariant 2 is specifically about `SET LOCAL` rather than `SET`. On a
    pooled connection, `SET` survives the transaction and the next request —
    a different customer — inherits it."""

    async def test_context_is_gone_after_the_transaction(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        alpha, _beta = two_tenants
        async with app_engine.connect() as conn:
            async with conn.begin():
                await conn.execute(text(f"SET LOCAL app.tenant_id = '{alpha}'"))
                assert await current_tenant(conn) == alpha
            # Same physical connection, new transaction.
            async with conn.begin():
                assert await current_tenant(conn) is None
                assert await rows_visible(conn, "leads") == 0

    async def test_a_pooled_connection_reused_by_another_tenant_is_clean(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        """The pool here holds two connections and no overflow, so a second
        request is guaranteed to land on a connection a previous request
        already used."""
        alpha, beta = two_tenants
        for _ in range(6):
            async with tenant_scope(alpha, engine=app_engine) as conn:
                assert await current_tenant(conn) == alpha
                assert await rows_visible(conn, "leads") == 1
            async with tenant_scope(beta, engine=app_engine) as conn:
                assert await current_tenant(conn) == beta
                result = await conn.execute(text("SELECT DISTINCT tenant_id FROM leads"))
                assert [row[0] for row in result] == [beta]

    async def test_context_is_cleared_even_when_the_body_raises(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        alpha, _beta = two_tenants
        with pytest.raises(RuntimeError, match="deliberate"):
            async with tenant_scope(alpha, engine=app_engine) as conn:
                await conn.execute(text("SELECT 1"))
                raise RuntimeError("deliberate")

        async with app_engine.connect() as conn, conn.begin():
            assert await current_tenant(conn) is None


class TestRolePrivileges:
    async def test_the_app_role_does_not_hold_bypassrls(self, engine: AsyncEngine):
        """If `mabel_app` ever gains BYPASSRLS, every other test in this file
        silently becomes meaningless."""
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'mabel_app'")
            )
            assert result.scalar_one() is False

    async def test_the_admin_role_does_hold_it(self, engine: AsyncEngine):
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'mabel_admin'")
            )
            assert result.scalar_one() is True

    async def test_the_app_role_can_see_the_schema(self, engine: AsyncEngine):
        """DROP SCHEMA public CASCADE + CREATE SCHEMA public leaves no USAGE
        grant. Without this, every app-role query says the table does not
        exist and RLS is never actually exercised."""
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT has_schema_privilege('mabel_app', 'public', 'USAGE')")
            )
            assert result.scalar_one() is True

    @pytest.mark.parametrize("table", TENANT_SCOPED_TABLES)
    async def test_rls_is_enabled_and_forced(self, engine: AsyncEngine, table: str):
        # ENABLE alone is not enough: the table owner bypasses it, and in a
        # managed Postgres the migration role often *is* the owner.
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname = :t AND relkind = 'r'"
                ),
                {"t": table},
            )
            enabled, forced = result.one()
            assert enabled, f"{table} does not have RLS enabled"
            assert forced, f"{table} has RLS enabled but not FORCEd; its owner reads everything"

    @pytest.mark.parametrize("table", TENANT_SCOPED_TABLES)
    async def test_every_scoped_table_has_a_policy(self, engine: AsyncEngine, table: str):
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT count(*) FROM pg_policies WHERE tablename = :t"), {"t": table}
            )
            assert result.scalar_one() >= 1, (
                f"{table} has RLS on and no policy: it returns nothing to anyone"
            )


class TestTenantScopeItself:
    async def test_a_non_uuid_tenant_is_refused_before_it_reaches_sql(
        self, app_engine: AsyncEngine
    ):
        """`SET LOCAL` takes no bind parameter, so the value is interpolated.
        Validation is therefore a safety boundary, not a style preference."""
        for bad in ["'; DROP TABLE leads; --", "not-a-uuid", "", None, 12345]:
            with pytest.raises(TenantScopeError):
                async with tenant_scope(bad, engine=app_engine):  # type: ignore[arg-type]
                    pass

    async def test_a_string_uuid_is_accepted(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        alpha, _beta = two_tenants
        async with tenant_scope(str(alpha), engine=app_engine) as conn:
            assert await current_tenant(conn) == alpha

    async def test_an_unknown_tenant_sees_nothing_rather_than_failing(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        async with tenant_scope(uuid4(), engine=app_engine) as conn:
            assert await rows_visible(conn, "leads") == 0


class TestAdminScope:
    """`admin_scope()` exists for the genuinely cross-tenant tables — the job
    queue, webhook receipts, the DID lookup that happens before a tenant is
    known. It does not grant BYPASSRLS, and this proves it."""

    async def test_admin_scope_requires_a_reason(self, app_engine: AsyncEngine):
        with pytest.raises(TenantScopeError, match="reason"):
            async with admin_scope(reason="", engine=app_engine):
                pass

    @pytest.mark.parametrize("table", ["leads", "calls", "contacts", "transcripts"])
    async def test_admin_scope_still_sees_no_tenant_data(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID], table: str
    ):
        async with admin_scope(reason="isolation test", engine=app_engine) as conn:
            assert await rows_visible(conn, table) == 0

    async def test_admin_scope_can_reach_the_global_tables(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        async with admin_scope(reason="claim a job", engine=app_engine) as conn:
            await conn.execute(
                text("INSERT INTO job_queue (kind, payload) VALUES ('test', '{}'::jsonb)")
            )
            assert await rows_visible(conn, "job_queue") >= 1
