"""Fixtures specific to the cross-tenant isolation suite.

The database fixtures themselves live in `tests/conftest.py`, because the
end-to-end suite needs the same ones.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


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
