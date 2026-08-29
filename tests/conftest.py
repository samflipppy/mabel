"""Database fixtures, shared by the isolation and integration suites.

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

**One pytest session per database.** The `schema` fixture drops and recreates
`public` when the session starts. Two sessions pointed at the same
`TEST_DATABASE_URL` will pull the schema out from under each other, and what
you see is a scatter of unique-violation and missing-relation errors in tests
that pass perfectly well on their own. If you want to run two suites at once,
give the second one its own database.

Anything that requests `engine`, `app_engine`, `two_tenants` or `shop` is
skipped when there is no database, rather than failing for a reason unrelated
to the code.
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

REPO = Path(__file__).resolve().parents[1]
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
    which none of these tests exercise. 0003, 0004 and 0005 are included: they
    carry the resolution functions, without which no inbound call can be routed,
    no inbound SMS attributed, no portal session resolved and no STOP from a
    customer honoured. 0007 adds the consent columns those sends are gated on.
    """
    parts = [
        "\n".join(_module(MIGRATIONS / "0001_v2_schema.py").SECTIONS),
        _module(MIGRATIONS / "0003_did_resolution.py").FUNCTION,
        _module(MIGRATIONS / "0004_sms_sender_resolution.py").FUNCTION,
        _module(MIGRATIONS / "0005_portal_session_resolution.py").FUNCTION,
        _module(MIGRATIONS / "0006_stripe_customer_resolution.py").FUNCTION,
        _module(MIGRATIONS / "0007_customer_messaging.py").COLUMNS,
        _module(MIGRATIONS / "0007_customer_messaging.py").FUNCTION,
        _module(MIGRATIONS / "0008_call_legs.py").TABLE,
    ]
    return "\n".join(parts)


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
    for item in items:
        # This hook is handed every item in the session, not just the ones that
        # need a database. Skipping indiscriminately would silently disable the
        # whole repo's tests, which happened once and is a very quiet way to
        # stop testing anything. So: only what actually asks for a db fixture.
        needs_db = {"engine", "app_engine", "two_tenants", "shop"} & set(
            getattr(item, "fixturenames", ())
        )
        if needs_db:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def schema() -> None:
    """Build the schema once, on its own event loop.

    Deliberately a *synchronous* fixture that runs its own loop internally.
    A session-scoped async fixture would need a session-scoped event loop, and
    the function-scoped async tests below cannot then share it — pytest-asyncio
    reports that as a ScopeMismatch, which is exactly what it did the first
    time this suite was pointed at a real database.

    Rebuilding the schema per test instead would work and would add ~70 DDL
    statements to every one of 137 tests for no benefit.
    """
    import asyncio

    url = _test_database_url()
    assert url is not None, "collection should have skipped this suite"

    async def build() -> None:
        import asyncpg

        # Raw asyncpg, not SQLAlchemy. `Connection.execute` with no parameters
        # uses the simple query protocol, which runs a whole script in one go.
        # SQLAlchemy prepares every statement, and a prepared statement cannot
        # contain multiple commands — so going through it means splitting the
        # DDL by hand, and a hand-written SQL splitter is a bug farm. The first
        # version of this file had one: it split on a trailing semicolon and
        # silently glued two statements together whenever a line ended in a
        # `-- comment` instead.
        conn = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
        try:
            # Start from nothing. A leftover table from a previous run with a
            # different policy would make this suite pass for the wrong reason.
            await conn.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
            await conn.execute(_load_sql())
        finally:
            await conn.close()

    asyncio.run(build())


@pytest_asyncio.fixture
async def engine(schema: None) -> AsyncIterator[AsyncEngine]:
    """A connection as the schema owner. Used for seeding and for inspecting
    catalogue tables, never to stand in for the application."""
    del schema
    url = _test_database_url()
    assert url is not None

    # NullPool: each test gets its own connections, so nothing carries over
    # between them and the pooling behaviour under test is app_engine's.
    admin = create_async_engine(url, poolclass=NullPool, connect_args={"statement_cache_size": 0})
    yield admin
    await admin.dispose()


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
