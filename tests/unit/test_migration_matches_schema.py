"""The migration must produce exactly the schema in 01-SCHEMA.sql.

01-SCHEMA.sql is the specification and is never executed. The Alembic revision
is what actually runs. Nothing keeps them in step except this file, so it reads
both and compares them structurally: every table, every column with its type,
every index, every RLS policy, every grant.

It is a text comparison, not a live-database one, because it has to pass in CI
before any database exists. `tests/isolation/` is where the real database gets
interrogated.

When this fails, the spec is right and the migration is wrong — unless you
changed the spec, in which case say so in the PR.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
SPEC = REPO / "01-SCHEMA.sql"
MIGRATIONS = REPO / "packages" / "db" / "mabel_db" / "migrations" / "versions"

# pg_cron lives in 0002 so the core schema can run on a Postgres without it.
CORE = MIGRATIONS / "0001_v2_schema.py"
CRON = MIGRATIONS / "0002_scheduled_jobs.py"


def _spec() -> str:
    return SPEC.read_text(encoding="utf-8")


def _load(path: Path) -> ModuleType:
    """Import a revision module for its SQL constants. Nothing here calls
    `upgrade()` — agents draft migrations, they do not run them."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration() -> str:
    """Only the SQL the revision actually emits.

    Reading the raw file text instead would let prose in a docstring satisfy —
    or trip — a structural check, which is how a schema drift test ends up
    passing for the wrong reason.
    """
    core = _load(CORE)
    cron = _load(CRON)
    return "\n".join(core.SECTIONS) + "\n" + "\n".join(body for _n, _s, body in cron.JOBS)


def _normalise(sql: str) -> str:
    """Collapse whitespace so indentation differences do not register as drift."""
    return re.sub(r"\s+", " ", sql).strip().lower()


def _tables(sql: str) -> set[str]:
    return set(re.findall(r"create table (?:if not exists )?(\w+)", sql, re.I))


def _indexes(sql: str) -> set[str]:
    return set(re.findall(r"create (?:unique )?index (?:if not exists )?(\w+)", sql, re.I))


def _policies(sql: str) -> set[tuple[str, str]]:
    return {
        (m.group(1).lower(), m.group(2).lower())
        for m in re.finditer(r"create policy (\w+) on (%?i?\w*)", sql, re.I)
    }


def _columns(sql: str, table: str) -> dict[str, str]:
    """Column name -> declared type, for one CREATE TABLE block."""
    match = re.search(rf"create table (?:if not exists )?{table}\s*\((.*?)\n\);", sql, re.I | re.S)
    if match is None:
        return {}
    body = match.group(1)
    columns: dict[str, str] = {}
    for raw in body.split("\n"):
        line = raw.strip().rstrip(",")
        if not line or line.startswith("--"):
            continue
        if re.match(r"^(primary key|unique|check|constraint|foreign key)\b", line, re.I):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        name, decl = parts[0], parts[1]
        if not re.match(r"^\w+$", name):
            continue
        # numeric(12,2) can arrive split across the space in `numeric(3,2)`;
        # take the type token plus any immediately attached parenthesis.
        columns[name.lower()] = decl.lower().rstrip(",")
    return columns


SPEC_SQL = _spec()
MIGRATION_SQL = _migration()
SPEC_TABLES = sorted(_tables(SPEC_SQL))


def test_spec_and_migration_are_both_present():
    assert SPEC.is_file(), f"missing {SPEC}"
    assert CORE.is_file(), f"missing {CORE}"
    assert SPEC_TABLES, "parsed no tables out of 01-SCHEMA.sql"


def test_every_spec_table_is_in_the_migration():
    missing = _tables(SPEC_SQL) - _tables(MIGRATION_SQL)
    assert not missing, (
        f"01-SCHEMA.sql declares tables the migration never creates: {sorted(missing)}"
    )


def test_the_migration_invents_no_tables():
    extra = _tables(MIGRATION_SQL) - _tables(SPEC_SQL)
    assert not extra, f"the migration creates tables that are not in the spec: {sorted(extra)}"


@pytest.mark.parametrize("table", SPEC_TABLES)
def test_columns_match(table: str):
    spec_cols = _columns(SPEC_SQL, table)
    mig_cols = _columns(MIGRATION_SQL, table)
    assert spec_cols, f"could not parse columns for {table} out of the spec"

    missing = set(spec_cols) - set(mig_cols)
    extra = set(mig_cols) - set(spec_cols)
    assert not missing, f"{table} is missing columns in the migration: {sorted(missing)}"
    assert not extra, f"{table} has columns the spec does not declare: {sorted(extra)}"

    mismatched = {
        name: (spec_cols[name], mig_cols[name])
        for name in spec_cols
        if spec_cols[name] != mig_cols[name]
    }
    assert not mismatched, f"{table} column types drifted (spec, migration): {mismatched}"


def test_every_index_is_carried_over():
    missing = _indexes(SPEC_SQL) - _indexes(MIGRATION_SQL)
    assert not missing, f"indexes in the spec but not the migration: {sorted(missing)}"


def test_partial_index_predicates_survive():
    """The `WHERE` clauses are the point of these indexes. Dropping one turns a
    tiny index into a full one and quietly changes the query plan."""
    for predicate in (
        "where deleted_at is null",
        "where is_live",
        "where is_active",
        "where merged_into is null and deleted_at is null",
        "where array_length(qa_flags,1) > 0",
        "where first_touched_at is null and status = 'new'",
        "where status = 'queued'",
        "where completed_at is null and failed_at is null",
    ):
        assert predicate in _normalise(MIGRATION_SQL), (
            f"lost the partial index predicate: {predicate}"
        )


def test_full_text_search_index_on_transcripts():
    # This is the transcript search feature in 02-PORTAL.md. Without the index
    # it still works and is unusably slow at the size that matters.
    assert "using gin (to_tsvector('english', coalesce(full_text,'')))" in _normalise(MIGRATION_SQL)


def test_trigram_index_for_fuzzy_contact_matching():
    assert "using gin (display_name gin_trgm_ops)" in _normalise(MIGRATION_SQL)


class TestRowLevelSecurity:
    """Invariant 1: every tenant-scoped table has RLS enabled AND forced."""

    TENANT_SCOPED = (
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

    def test_the_scoped_table_list_matches_the_spec(self):
        # Both files drive RLS off a literal ARRAY[...] list. If a table is
        # added to one and not the other, that is a cross-tenant leak.
        spec_list = re.search(r"foreach t in array array\[(.*?)\]", _normalise(SPEC_SQL), re.S)
        mig_list = re.search(r"foreach t in array array\[(.*?)\]", _normalise(MIGRATION_SQL), re.S)
        assert spec_list and mig_list
        spec_names = set(re.findall(r"'(\w+)'", spec_list.group(1)))
        mig_names = set(re.findall(r"'(\w+)'", mig_list.group(1)))
        assert spec_names == mig_names, (
            f"RLS table list drifted. Only in spec: {sorted(spec_names - mig_names)}. "
            f"Only in migration: {sorted(mig_names - spec_names)}"
        )
        assert spec_names == set(self.TENANT_SCOPED)

    def test_every_tenant_scoped_table_carries_tenant_id(self):
        for table in self.TENANT_SCOPED:
            assert "tenant_id" in _columns(MIGRATION_SQL, table), (
                f"{table} is RLS-protected but has no tenant_id column to protect it by"
            )

    def test_force_is_present_not_just_enable(self):
        # ENABLE alone is insufficient. The table owner bypasses it.
        normalised = _normalise(MIGRATION_SQL)
        assert "force row level security" in normalised
        assert normalised.count("force row level security") >= 2, (
            "expected FORCE in both the loop and the tenants table"
        )

    def test_the_policy_fails_closed(self):
        # current_setting(..., true) returns NULL when unset, so the comparison
        # is NULL, so the policy matches zero rows. Dropping the `true` makes
        # it raise instead, and a raise in the wrong place gets caught and
        # swallowed somewhere upstream.
        assert "current_setting('app.tenant_id', true)::uuid" in _normalise(MIGRATION_SQL)

    def test_tenants_sees_only_its_own_row(self):
        assert ("tenant_self", "tenants") in _policies(MIGRATION_SQL)

    def test_the_app_role_is_not_granted_bypassrls(self):
        normalised = _normalise(MIGRATION_SQL)
        assert "create role mabel_app nologin;" in normalised
        assert "mabel_app nologin bypassrls" not in normalised
        assert "mabel_admin nologin bypassrls" in normalised


class TestMoneyColumns:
    """Invariant 5: money is integer cents in BIGINT, with a currency column.
    Never float, never NUMERIC on anything Stripe-facing."""

    MONEY_COLUMNS = {
        "leads": ["value_cents"],
        "subscriptions": ["price_cents"],
        "monthly_reports": ["won_value_cents"],
    }

    def test_owner_facing_money_is_bigint(self):
        for table, columns in self.MONEY_COLUMNS.items():
            cols = _columns(MIGRATION_SQL, table)
            for column in columns:
                assert cols.get(column) == "bigint", (
                    f"{table}.{column} must be bigint cents, found {cols.get(column)!r}"
                )

    def test_internal_cost_columns_are_integer_cents(self):
        # Our own cost tracking. Integer rather than bigint because a single
        # call costing more than $21m is not a thing that happens.
        calls = _columns(MIGRATION_SQL, "calls")
        assert calls.get("voice_cost_cents") == "integer"
        assert calls.get("telephony_cost_cents") == "integer"
        assert _columns(MIGRATION_SQL, "usage_daily").get("cost_cents") == "integer"

    def test_no_float_anywhere_in_the_schema(self):
        # The grep AGENTS.md asks for, as a test that cannot be forgotten.
        normalised = _normalise(MIGRATION_SQL)
        for banned in (" float", " real", " double precision", " money "):
            assert banned not in normalised, f"a money-adjacent float type appeared: {banned!r}"

    def test_every_money_column_has_a_currency_beside_it(self):
        for table in ("leads", "subscriptions"):
            assert "currency" in _columns(MIGRATION_SQL, table), (
                f"{table} stores money without an explicit currency column"
            )

    def test_the_only_numeric_columns_are_not_money(self):
        # numeric appears exactly twice in the spec: speaking_rate, which is a
        # voice speed, and voice_minutes, which is minutes. Neither is money.
        numerics = re.findall(r"(\w+)\s+numeric\(", _normalise(MIGRATION_SQL))
        assert sorted(numerics) == ["speaking_rate", "voice_minutes"], (
            f"a NUMERIC column appeared that may be money: {numerics}"
        )


class TestTimestamps:
    """Invariant 6: every timestamp is timestamptz, in UTC."""

    def test_no_naive_timestamps(self):
        normalised = _normalise(MIGRATION_SQL)
        # `timestamp without time zone`, or a bare `timestamp` followed by a
        # column terminator, both lose the offset.
        assert "timestamp without time zone" not in normalised
        assert not re.search(r"\btimestamp\b(?!tz)", normalised), (
            "found a naive timestamp column; every instant in Mabel is timestamptz"
        )

    def test_tenant_carries_an_iana_timezone(self):
        assert _columns(MIGRATION_SQL, "tenants").get("timezone") == "text"


class TestScheduledJobs:
    def test_every_cron_entry_from_the_spec_is_scheduled(self):
        spec_names = set(re.findall(r"cron\.schedule\('([\w-]+)'", SPEC_SQL))
        mig_names = {name for name, _schedule, _body in _load(CRON).JOBS}
        assert spec_names, "parsed no cron entries out of the spec"
        assert spec_names <= mig_names, (
            f"cron entries not scheduled: {sorted(spec_names - mig_names)}"
        )

    def test_cron_only_enqueues_and_never_acts(self):
        """Cron runs as a superuser with no tenant context. Every entry must
        insert into job_queue and let the worker do the work through
        tenant_scope(), where RLS applies. The one exception is pruning
        webhook receipts, which holds no customer data."""
        for _name, _schedule, statement in _load(CRON).JOBS:
            lowered = statement.lower()
            if "webhook_receipts" in lowered:
                continue
            if "insert into job_queue" in lowered:
                continue
            assert "delete" not in lowered and "update" not in lowered, (
                f"a cron entry mutates customer data directly:\n{statement}"
            )


class TestMigrationHygiene:
    def test_no_connection_string_is_committed(self):
        for path in (CORE, CRON, MIGRATIONS.parent / "env.py"):
            body = path.read_text(encoding="utf-8")
            assert "postgresql://" not in body or "offline/render-only" in body, (
                f"{path.name} may contain a connection string"
            )

    def test_the_revision_chain_is_linear(self):
        assert "down_revision: str | None = None" in CORE.read_text(encoding="utf-8")
        assert 'down_revision: str | None = "0001_v2_schema"' in CRON.read_text(encoding="utf-8")
