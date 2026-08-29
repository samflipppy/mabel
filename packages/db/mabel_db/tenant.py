"""`tenant_scope()` — the only way Mabel talks to Postgres.

Invariant 2: every tenant-scoped query runs inside an explicit transaction
that begins with `SET LOCAL app.tenant_id`. Never `SET`. Plain `SET` persists
for the life of the connection, and on a pooled connection that means the next
request — a different customer — inherits it. `SET LOCAL` dies with the
transaction, which is the whole point.

RLS fails closed. If `app.tenant_id` is unset, `current_setting(..., true)`
returns NULL, every policy compares against NULL, and every policy matches
zero rows. A query that forgets its tenant returns nothing rather than
everything. That is the correct direction to fail in.

The app connects as `mabel_app`, which is not the table owner and does not
hold BYPASSRLS. `mabel_admin` holds BYPASSRLS and is for migrations and
cross-tenant analytics only. Application code that reaches for it is a bug,
and `tests/isolation/` checks for exactly that.

Usage is meant to be hard to get wrong:

    async with tenant_scope(tenant_id) as conn:
        rows = await conn.execute(text("SELECT id FROM leads"))

There is no `SELECT ... WHERE tenant_id = :tenant_id` in that query, and there
should not be. The database is doing it.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

# Postgres will not take a bind parameter in SET LOCAL, so the value is
# interpolated. That makes validating it non-negotiable rather than stylistic:
# a tenant id is a UUID, we parse it as one, and anything else raises before it
# reaches a statement.
_SET_LOCAL_TENANT = "SET LOCAL app.tenant_id = '{tenant_id}'"

_engine: AsyncEngine | None = None


class TenantScopeError(RuntimeError):
    """Raised when tenant context is missing, malformed, or being bypassed."""


class DatabaseUnavailable(RuntimeError):
    """Raised when DATABASE_URL is unset. We fail closed rather than falling
    back to a local default that would quietly connect somewhere unintended.
    See docs/BLOCKED.md #1."""


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise DatabaseUnavailable(
            "DATABASE_URL is unset. Mabel does not guess at a connection string. "
            "See docs/BLOCKED.md #1."
        )
    if "+asyncpg" not in url:
        # Supabase hands out a `postgresql://` URL; SQLAlchemy needs the driver
        # named explicitly for the async engine.
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def get_engine() -> AsyncEngine:
    """One engine per process. Pooled, which is exactly why SET LOCAL matters."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            database_url(),
            pool_size=int(os.environ.get("DB_POOL_SIZE", "10")),
            max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "5")),
            pool_pre_ping=True,
            # Supabase's pooler does not support prepared statement caching
            # across connections; naming them collides.
            connect_args={"statement_cache_size": 0},
        )
    return _engine


async def dispose_engine() -> None:
    """Close the pool. Called on process shutdown, and by tests between cases."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


def _validated(tenant_id: UUID | str) -> UUID:
    if isinstance(tenant_id, UUID):
        return tenant_id
    try:
        return UUID(str(tenant_id))
    except (ValueError, AttributeError, TypeError) as exc:
        # This is the guard that keeps SET LOCAL interpolation safe. It is also
        # the guard that catches a tenant identifier arriving from somewhere it
        # should not have — a tool argument, say.
        raise TenantScopeError(f"tenant_id must be a UUID, got {tenant_id!r}") from exc


@asynccontextmanager
async def tenant_scope(
    tenant_id: UUID | str, *, engine: AsyncEngine | None = None
) -> AsyncIterator[AsyncConnection]:
    """Open a transaction, set the tenant, yield the connection.

    Commits on clean exit, rolls back on exception. The `SET LOCAL` is scoped
    to this transaction and cannot outlive it, so returning the connection to
    the pool cannot leak one customer's context into the next one's request.
    """
    scoped = _validated(tenant_id)
    conn_engine = engine or get_engine()

    async with conn_engine.connect() as conn, conn.begin():
        await conn.execute(text(_SET_LOCAL_TENANT.format(tenant_id=scoped)))
        yield conn


@asynccontextmanager
async def admin_scope(
    *, reason: str, engine: AsyncEngine | None = None
) -> AsyncIterator[AsyncConnection]:
    """A transaction with no tenant context, for the handful of operations that
    are genuinely cross-tenant: the worker claiming from `job_queue`, webhook
    idempotency, DID lookup before a tenant is known.

    Every one of those touches a table that is deliberately not tenant-scoped
    in 01-SCHEMA.sql. This does **not** grant BYPASSRLS — the connection is
    still `mabel_app`, so any tenant-scoped table it touches returns zero rows.
    The RLS fail-closed default is what makes this safe to exist.

    `reason` is required and logged. If you cannot write one, you want
    `tenant_scope()`.
    """
    if not reason or not reason.strip():
        raise TenantScopeError("admin_scope requires a reason")

    conn_engine = engine or get_engine()
    async with conn_engine.connect() as conn, conn.begin():
        # Belt and braces: make it explicit that no tenant is in scope, so
        # a connection recycled from a tenant-scoped request cannot carry
        # one in. SET LOCAL already guarantees this; saying so costs
        # nothing and documents the intent at the call site.
        await conn.execute(text("SET LOCAL app.tenant_id = ''"))
        yield conn


async def current_tenant(conn: AsyncConnection) -> UUID | None:
    """What tenant does this connection think it is in? For assertions in
    tests and for the span attribute on a traced query."""
    result = await conn.execute(text("SELECT current_setting('app.tenant_id', true)"))
    raw = result.scalar_one_or_none()
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None
