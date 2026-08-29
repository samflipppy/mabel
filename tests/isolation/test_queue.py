"""The queue, against a real Postgres.

`SKIP LOCKED` cannot be tested without one — the whole property is what two
concurrent transactions do to each other, and that is the database's behaviour,
not ours.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabel_db.tenant import admin_scope
from mabel_worker import queue

pytestmark = pytest.mark.asyncio


async def _clear(engine: AsyncEngine) -> None:
    async with admin_scope(reason="test cleanup", engine=engine) as conn:
        await conn.execute(text("DELETE FROM job_queue"))


class TestClaiming:
    async def test_a_queued_job_is_claimed(self, app_engine: AsyncEngine, two_tenants):
        alpha, _beta = two_tenants
        await _clear(app_engine)
        await queue.enqueue(app_engine, kind="morning_recap", tenant_id=alpha)

        claimed = await queue.claim(app_engine)
        assert len(claimed) == 1
        assert claimed[0].kind == "morning_recap"
        assert claimed[0].tenant_id == alpha
        assert claimed[0].attempts == 1

    async def test_a_claimed_job_is_not_claimed_again(self, app_engine: AsyncEngine, two_tenants):
        alpha, _beta = two_tenants
        await _clear(app_engine)
        await queue.enqueue(app_engine, kind="morning_recap", tenant_id=alpha)

        assert len(await queue.claim(app_engine)) == 1
        assert await queue.claim(app_engine) == []

    async def test_two_workers_never_take_the_same_job(self, app_engine: AsyncEngine, two_tenants):
        """The property SKIP LOCKED exists for. Without it, two workers send
        the same owner the same recap twice."""
        alpha, _beta = two_tenants
        await _clear(app_engine)
        for _ in range(6):
            await queue.enqueue(app_engine, kind="morning_recap", tenant_id=alpha)

        first, second = await asyncio.gather(
            queue.claim(app_engine, limit=3), queue.claim(app_engine, limit=3)
        )
        ids = [job.id for job in first + second]
        assert len(ids) == len(set(ids)), "two workers claimed the same job"

    async def test_a_future_job_is_not_claimed_yet(self, app_engine: AsyncEngine, two_tenants):
        from datetime import UTC, datetime, timedelta

        alpha, _beta = two_tenants
        await _clear(app_engine)
        await queue.enqueue(
            app_engine,
            kind="morning_recap",
            tenant_id=alpha,
            run_after=datetime.now(UTC) + timedelta(hours=1),
        )
        assert await queue.claim(app_engine) == []

    async def test_the_batch_size_is_respected(self, app_engine: AsyncEngine, two_tenants):
        alpha, _beta = two_tenants
        await _clear(app_engine)
        for _ in range(10):
            await queue.enqueue(app_engine, kind="morning_recap", tenant_id=alpha)
        assert len(await queue.claim(app_engine, limit=4)) == 4


class TestAbandonedJobsRecover:
    async def test_a_job_locked_longer_than_the_lease_is_reclaimed(
        self, app_engine: AsyncEngine, two_tenants
    ):
        """A worker that died mid-job must not leave the owner's recap locked
        forever."""
        alpha, _beta = two_tenants
        await _clear(app_engine)
        job_id = await queue.enqueue(app_engine, kind="morning_recap", tenant_id=alpha)

        async with admin_scope(reason="simulate a dead worker", engine=app_engine) as conn:
            await conn.execute(
                text(
                    "UPDATE job_queue SET locked_at = now() - make_interval(secs => :old), "
                    "locked_by = 'dead-worker' WHERE id = :id"
                ),
                {"id": job_id, "old": queue.LEASE_SECONDS + 60},
            )

        reclaimed = await queue.claim(app_engine)
        assert [job.id for job in reclaimed] == [job_id]

    async def test_a_recently_locked_job_is_left_alone(self, app_engine: AsyncEngine, two_tenants):
        # A slow-but-alive job must not be stolen out from under itself.
        alpha, _beta = two_tenants
        await _clear(app_engine)
        await queue.enqueue(app_engine, kind="morning_recap", tenant_id=alpha)
        await queue.claim(app_engine)
        assert await queue.claim(app_engine) == []


class TestSettlingJobs:
    async def test_completing_takes_it_out_of_the_queue(self, app_engine: AsyncEngine, two_tenants):
        alpha, _beta = two_tenants
        await _clear(app_engine)
        job_id = await queue.enqueue(app_engine, kind="morning_recap", tenant_id=alpha)
        (job,) = await queue.claim(app_engine)
        await queue.complete(app_engine, job.id)

        counts = await queue.depth(app_engine)
        assert counts["ready"] == 0
        assert counts["in_flight"] == 0
        del job_id

    async def test_a_retry_comes_back_later_not_now(self, app_engine: AsyncEngine, two_tenants):
        alpha, _beta = two_tenants
        await _clear(app_engine)
        await queue.enqueue(app_engine, kind="morning_recap", tenant_id=alpha)
        (job,) = await queue.claim(app_engine)
        await queue.retry_later(app_engine, job, "telnyx timed out")

        # Backed off, so not immediately ready.
        assert await queue.claim(app_engine) == []

        async with admin_scope(reason="inspect", engine=app_engine) as conn:
            row = await conn.execute(
                text("SELECT last_error, locked_by, run_after > now() AS deferred FROM job_queue")
            )
            found = row.mappings().one()
            assert found["last_error"] == "telnyx timed out"
            assert found["locked_by"] is None
            assert found["deferred"] is True

    async def test_the_last_attempt_fails_for_good(self, app_engine: AsyncEngine, two_tenants):
        alpha, _beta = two_tenants
        await _clear(app_engine)
        await queue.enqueue(app_engine, kind="morning_recap", tenant_id=alpha, max_attempts=1)
        (job,) = await queue.claim(app_engine)
        await queue.retry_later(app_engine, job, "gave up")

        counts = await queue.depth(app_engine)
        assert counts["failed"] == 1
        assert counts["ready"] == 0

    async def test_a_failed_job_is_kept_not_deleted(self, app_engine: AsyncEngine, two_tenants):
        """A job that failed five times is the most interesting row in the
        table."""
        alpha, _beta = two_tenants
        await _clear(app_engine)
        job_id = await queue.enqueue(app_engine, kind="x", tenant_id=alpha)
        await queue.fail(app_engine, job_id, "nothing handles this")

        async with admin_scope(reason="inspect", engine=app_engine) as conn:
            row = await conn.execute(
                text("SELECT failed_at, last_error FROM job_queue WHERE id = :id"),
                {"id": job_id},
            )
            found = row.mappings().one()
            assert found["failed_at"] is not None
            assert "nothing handles" in found["last_error"]


class TestTheQueueIsNotTenantScopedButTheWorkIs:
    async def test_a_worker_sees_every_tenants_jobs(
        self, app_engine: AsyncEngine, two_tenants: tuple[UUID, UUID]
    ):
        """job_queue is deliberately not a tenant-scoped table: a worker has to
        see every tenant's jobs to claim any."""
        alpha, beta = two_tenants
        await _clear(app_engine)
        await queue.enqueue(app_engine, kind="morning_recap", tenant_id=alpha)
        await queue.enqueue(app_engine, kind="morning_recap", tenant_id=beta)

        claimed = await queue.claim(app_engine)
        assert {job.tenant_id for job in claimed} == {alpha, beta}

    async def test_claiming_still_cannot_read_tenant_data(
        self, app_engine: AsyncEngine, two_tenants
    ):
        """admin_scope grants no BYPASSRLS. Seeing the queue does not mean
        seeing anybody's leads."""
        from .conftest import rows_visible

        await _clear(app_engine)
        async with admin_scope(reason="claim", engine=app_engine) as conn:
            assert await rows_visible(conn, "leads") == 0
            assert await rows_visible(conn, "calls") == 0


class TestDepth:
    async def test_it_counts_what_the_pager_needs(self, app_engine: AsyncEngine, two_tenants):
        alpha, _beta = two_tenants
        await _clear(app_engine)
        for _ in range(3):
            await queue.enqueue(app_engine, kind="morning_recap", tenant_id=alpha)

        assert (await queue.depth(app_engine))["ready"] == 3
        await queue.claim(app_engine, limit=2)
        counts = await queue.depth(app_engine)
        assert counts["in_flight"] == 2
        assert counts["ready"] == 1
