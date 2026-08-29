"""Post-call persistence against a real database.

Invariant 7 says every transcript and recording is copied to our own storage
post-call. These tests check the half of that which is ours: that the call, the
transcript, the thread row and the usage all land, in one transaction, in the
right tenant.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabel_db.tenant import tenant_scope
from mabel_media.postcall import CallOutcome, finalize

pytestmark = pytest.mark.asyncio

STARTED = datetime(2026, 10, 14, 6, 30, tzinfo=UTC)


def outcome(tenant_id: UUID, **overrides) -> CallOutcome:
    base = {
        "call_id": "call_persist_1",
        "tenant_id": tenant_id,
        "timezone": "America/New_York",
        "trade": "plumbing",
        "from_e164": "+12165550148",
        "to_e164": "+12165550199",
        "started_at": STARTED,
        "ended_at": STARTED + timedelta(minutes=3),
        "turns": [
            {"role": "assistant", "text": "Thanks for calling Ruiz Plumbing."},
            {"role": "caller", "text": "My water heater is leaking all over the floor."},
        ],
        "tool_trace": [{"tool": "create_lead", "ok": True}],
    }
    return CallOutcome(**(base | overrides))


class TestEverythingLands:
    async def test_the_call_transcript_event_and_usage_all_get_written(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        alpha, _beta = two_tenants
        await finalize(outcome(alpha), storage=None, engine=app_engine)

        async with tenant_scope(alpha, engine=app_engine) as conn:
            call_row = await conn.execute(
                text(
                    "SELECT id, duration_sec, voice_cost_cents, outcome, archived_at "
                    "FROM calls WHERE xai_call_id = 'call_persist_1'"
                )
            )
            row = call_row.mappings().one()
            assert row["duration_sec"] == 180
            assert row["voice_cost_cents"] == 25
            assert row["archived_at"] is not None

            transcript = await conn.execute(
                text("SELECT full_text, tool_trace FROM transcripts WHERE call_id = :c"),
                {"c": row["id"]},
            )
            stored = transcript.mappings().one()
            assert "water heater" in stored["full_text"]
            assert stored["tool_trace"][0]["tool"] == "create_lead"

            events = await conn.execute(
                text("SELECT count(*) FROM communication_events WHERE kind = 'call'")
            )
            assert events.scalar_one() >= 1

            usage = await conn.execute(
                text(
                    "SELECT calls_answered, voice_minutes, cost_cents FROM usage_daily "
                    "WHERE day = :day"
                ),
                {"day": STARTED.date()},
            )
            counted = usage.mappings().one()
            assert counted["calls_answered"] == 1
            assert float(counted["voice_minutes"]) == 3.0

    async def test_the_transcript_is_findable_by_full_text_search(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        """02-PORTAL.md: 'search for that guy who called about the water
        heater' and it finds him. This is that, against the real index."""
        alpha, _beta = two_tenants
        await finalize(outcome(alpha), storage=None, engine=app_engine)

        async with tenant_scope(alpha, engine=app_engine) as conn:
            found = await conn.execute(
                text(
                    "SELECT count(*) FROM transcripts "
                    "WHERE to_tsvector('english', coalesce(full_text,'')) "
                    "@@ plainto_tsquery('english', 'water heater')"
                )
            )
            assert found.scalar_one() == 1

    async def test_another_tenant_cannot_see_the_call(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        alpha, beta = two_tenants
        await finalize(outcome(alpha), storage=None, engine=app_engine)

        async with tenant_scope(beta, engine=app_engine) as conn:
            found = await conn.execute(
                text("SELECT count(*) FROM calls WHERE xai_call_id = 'call_persist_1'")
            )
            assert found.scalar_one() == 0

            transcripts = await conn.execute(text("SELECT count(*) FROM transcripts"))
            assert transcripts.scalar_one() == 0


class TestArchivingTwiceIsSafe:
    async def test_a_retried_finalize_does_not_duplicate_the_call(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        """The worker retries. `xai_call_id` is unique and the insert upserts,
        so a retry updates rather than creating a second call row."""
        alpha, _beta = two_tenants
        await finalize(outcome(alpha), storage=None, engine=app_engine)
        await finalize(outcome(alpha), storage=None, engine=app_engine)

        async with tenant_scope(alpha, engine=app_engine) as conn:
            found = await conn.execute(
                text("SELECT count(*) FROM calls WHERE xai_call_id = 'call_persist_1'")
            )
            assert found.scalar_one() == 1


class TestUsageAccumulates:
    async def test_two_calls_on_one_day_add_up(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        alpha, _beta = two_tenants
        await finalize(outcome(alpha, call_id="call_a"), storage=None, engine=app_engine)
        await finalize(outcome(alpha, call_id="call_b"), storage=None, engine=app_engine)

        async with tenant_scope(alpha, engine=app_engine) as conn:
            usage = await conn.execute(
                text("SELECT calls_answered, voice_minutes FROM usage_daily WHERE day = :day"),
                {"day": STARTED.date()},
            )
            row = usage.mappings().one()
            assert row["calls_answered"] == 2
            assert float(row["voice_minutes"]) == 6.0

    async def test_the_cost_stays_an_integer_number_of_cents(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        alpha, _beta = two_tenants
        await finalize(outcome(alpha), storage=None, engine=app_engine)

        async with tenant_scope(alpha, engine=app_engine) as conn:
            cost = await conn.execute(
                text("SELECT cost_cents FROM usage_daily WHERE day = :day"),
                {"day": STARTED.date()},
            )
            value = cost.scalar_one()
            assert isinstance(value, int)
            assert value == 25
