"""The DDL the isolation suite applies.

This file used to test a hand-written SQL splitter. There is no splitter any
more: the schema is executed as one script through asyncpg's simple query
protocol, because the splitter was a bug farm and duly produced a bug — it
split on a trailing semicolon, so a line ending in `-- comment` glued two
statements together and asyncpg refused the result.

What is left worth checking is that the script the suite applies is the whole
schema and nothing has fallen out of it. Runs without a database.
"""

from __future__ import annotations

from tests.conftest import _load_sql

SQL = _load_sql()


def test_it_is_one_coherent_script():
    # Dollar-quoted blocks are what a splitter used to be able to cut in half.
    # Executing the script whole makes that impossible, and this confirms the
    # source is balanced in the first place.
    assert SQL.count("$$") % 2 == 0
    assert SQL.count("$f$") % 2 == 0


def test_the_rls_block_is_present():
    assert "CREATE POLICY tenant_isolation" in SQL
    assert "FORCE ROW LEVEL SECURITY" in SQL
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" in SQL


def test_the_fail_closed_cast_survives():
    # nullif() is what makes an empty app.tenant_id return nothing rather than
    # raising. See the DEVIATION note in migration 0001.
    assert "nullif(current_setting('app.tenant_id', true), '')::uuid" in SQL


def test_the_resolution_functions_are_included():
    """Without these no inbound call is routed, no SMS is attributed, no portal
    session resolves, and no Stripe event finds its tenant."""
    for name in (
        "resolve_tenant_by_did",
        "resolve_user_by_phone",
        "resolve_user_by_supabase_uid",
        "resolve_tenant_by_stripe_customer",
    ):
        assert f"CREATE OR REPLACE FUNCTION {name}" in SQL, f"{name} is missing"


def test_every_definer_function_pins_its_search_path():
    """A SECURITY DEFINER function without one is the classic Postgres
    escalation: the caller points `tenants` at a table they control.

    Counts declarations rather than occurrences of the phrase — a comment
    mentioning SECURITY DEFINER is not a function, and counting text made this
    fail for the wrong reason.
    """
    declarations = [line for line in SQL.splitlines() if line.strip() == "SECURITY DEFINER"]
    pins = [
        line for line in SQL.splitlines() if line.strip() == "SET search_path = public, pg_temp"
    ]
    assert len(declarations) == 4, f"expected four definer functions, found {len(declarations)}"
    assert len(pins) == len(declarations)


def test_the_uuidv7_function_is_there():
    assert "CREATE OR REPLACE FUNCTION uuidv7()" in SQL
    assert "LANGUAGE sql VOLATILE" in SQL


def test_the_extensions_the_schema_needs_are_created():
    for extension in ("pgcrypto", "citext", "pg_trgm"):
        assert f"CREATE EXTENSION IF NOT EXISTS {extension}" in SQL


def test_pg_cron_is_not_in_this_script():
    """0002 is deliberately excluded: pg_cron is a superuser extension a
    scratch Postgres does not have, and none of these tests exercise it."""
    assert "pg_cron" not in SQL
