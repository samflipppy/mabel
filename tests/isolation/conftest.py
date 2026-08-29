"""Fixtures for the cross-tenant isolation suite.

These are the only tests that need a real Postgres. RLS cannot be tested
against a mock — the whole point is that the *database* refuses, not that our
code remembers to filter.

**On running migrations.** Agents do not run migrations. That rule is about
production, and it stands. This fixture builds an ephemeral schema in a
throwaway database named by `TEST_DATABASE_URL`, and it refuses to touch
anything that looks like production. If you cannot give it a scratch database,
the suite skips loudly rather than pretending to have passed.

Set it up locally with:

    docker run -d --name mabel-test -e POSTGRES_PASSWORD=postgres \\
      -p 55432:5432 postgres:16
    export TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:55432/postgres

CI runs the same thing as a service container. See docs/BLOCKED.md #1 for what
is still missing before these run against a Supabase-shaped database.
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

REPO = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO / "packages" / "db" / "mabel_db" / "migrations" / "versions"

SKIP_REASON = (
    "TEST_DATABASE_URL is unset, so RLS cannot be exercised. These tests assert that "
    "the database refuses cross-tenant reads; there is no way to check that without a "
    "database, and faking it would be worse than skipping. See tests/isolation/conftest.py."
)

# A scratch database is one we are allowed to create and drop tables in. If the
# URL looks like it could be a real one, we stop. The cost of being wrong here
# is somebody's call history.
PRODUCTION_SHAPED = re.compile(r"(prod|production|supabase\.co|\.fly\.dev|amazonaws\.com)", re.I)


def _test_database_url() -> str | None:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        return None
    if PRODUCTION_SHAPED.search(url):
        pytest.fail(
            "TEST_DATABASE_URL looks like a real database: "
            f"{PRODUCTION_SHAPED.search(url).group(0)!r}. "  # type: ignore[union-attr]
            "This suite creates and drops tables. Point it at a scratch database."
        )
    if "+asyncpg" not in url:
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _module(path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_sql() -> str:
    """The DDL these tests need, as SQL. Executed directly rather than through
    Alembic, so the test schema is unambiguously these revisions and nothing
    carries over between runs.

    0002 is skipped: it is pg_cron, which a scratch Postgres does not have and
    which none of these tests exercise. 0003 is included, because it carries
    the DID resolution function, without which no call can be routed at all.
    """
    core = _module(MIGRATIONS / "0001_v2_schema.py")
    did = _module(MIGRATIONS / "0003_did_resolution.py")
    return "\n".join(core.SECTIONS) + "\n" + did.FUNCTION


def pytest_collection_modifyitems(config, items):
    """Skip the whole suite at collection time when there is no database.

    Deliberately not a `pytest.skip()` inside the engine fixture: skipping from
    inside an async fixture confuses pytest-asyncio into an assertion error, so
    the suite would fail for an unrelated reason and hide the real one, which is
    that RLS went unverified.
    """
    if _test_database_url() is not None:
        return
    skip = pytest.mark.skip(reason=SKIP_REASON)
    here = Path(__file__).parent
    for item in items:
        # This hook is handed every item in the session, not just the ones
        # under this directory. Without the path filter it skips the whole
        # repo's tests, which is a very quiet way to stop testing anything.
        if here in Path(str(item.fspath)).parents:
            item.add_marker(skip)


@pytest_asyncio.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    url = _test_database_url()
    assert url is not None, "collection should have skipped this suite"

    admin = create_async_engine(url, poolclass=None, connect_args={"statement_cache_size": 0})

    async with admin.begin() as conn:
        # Start from nothing. A leftover table from a previous run with a
        # different policy would make this suite pass for the wrong reason.
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        for statement in _split(_load_sql()):
            await conn.execute(text(statement))

    yield admin
    await admin.dispose()


def _split(sql: str) -> list[str]:
    """Split on semicolons that are not inside a dollar-quoted block. The RLS
    section is one big DO $$ ... $$ and must not be cut in half."""
    statements: list[str] = []
    buffer: list[str] = []
    in_dollar = False
    for line in sql.split("\n"):
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        # Count dollar-quote openers/closers ($$ and $f$).
        for token in re.findall(r"\$\w*\$", line):
            del token
            in_dollar = not in_dollar
        buffer.append(line)
        if not in_dollar and stripped.endswith(";"):
            statement = "\n".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
    tail = "\n".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


@pytest_asyncio.fixture
async def app_engine(engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """A connection pool that behaves like the application's: connects as a
    role with RLS forced on it, and pools connections so a leaked `SET` would
    be visible."""
    url = _test_database_url()
    assert url is not None

    # The app role in production is `mabel_app`, a NOLOGIN role the connection
    # string assumes into. Here we get the same effect with SET ROLE per
    # connection, which is what actually matters: not the table owner, no
    # BYPASSRLS.
    app = create_async_engine(
        url,
        pool_size=2,
        max_overflow=0,
        connect_args={"statement_cache_size": 0, "server_settings": {"role": "mabel_app"}},
    )
    yield app
    await app.dispose()


@pytest_asyncio.fixture
async def two_tenants(engine: AsyncEngine) -> AsyncIterator[tuple[UUID, UUID]]:
    """Two tenants with a lead, a call, and a contact each. Seeded as the
    owner, because seeding through the app role is the thing under test."""
    alpha, beta = uuid4(), uuid4()

    async with engine.begin() as conn:
        for tenant_id, name, did in (
            (alpha, "Ruiz Plumbing", "+12165550148"),
            (beta, "Delgado HVAC", "+12165550199"),
        ):
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, business_name, trade, did_e164, status) "
                    "VALUES (:id, :name, 'plumbing', :did, 'active')"
                ),
                {"id": tenant_id, "name": name, "did": did},
            )
            await conn.execute(
                text(
                    "INSERT INTO contacts (tenant_id, display_name, primary_phone) "
                    "VALUES (:t, :n, :p)"
                ),
                {"t": tenant_id, "n": f"{name} caller", "p": "+12165550001"},
            )
            await conn.execute(
                text(
                    "INSERT INTO leads (tenant_id, caller_name, job_type, value_cents) "
                    "VALUES (:t, :n, 'water heater', 380000)"
                ),
                {"t": tenant_id, "n": f"{name} lead"},
            )
            await conn.execute(
                text(
                    "INSERT INTO calls (tenant_id, started_at, from_e164, to_e164) "
                    "VALUES (:t, now(), '+12165550001', :did)"
                ),
                {"t": tenant_id, "did": did},
            )

    yield alpha, beta

    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM tenants WHERE id = ANY(:ids)"), {"ids": [alpha, beta]})


async def rows_visible(conn: AsyncConnection, table: str) -> int:
    result = await conn.execute(text(f"SELECT count(*) FROM {table}"))
    return int(result.scalar_one())
