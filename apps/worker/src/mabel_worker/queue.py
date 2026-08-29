"""The queue. `SELECT ... FOR UPDATE SKIP LOCKED`.

Postgres as a job queue, which for this volume is the right answer: no second
piece of infrastructure to operate, and a job can be enqueued in the same
transaction as the row it is about. `escalate_emergency` writing a lead and
queueing an alert atomically is only possible because they are the same
database.

`SKIP LOCKED` is what makes several workers safe. Each one takes rows nobody
else has locked and steps over the rest, so two workers never run the same job
and neither waits on the other.

**Claiming is not tenant-scoped, and doing the work is.** `job_queue` is
deliberately not a tenant-scoped table in 01-SCHEMA.sql — a worker has to see
every tenant's jobs to claim any. So claiming runs through `admin_scope()`,
which grants no BYPASSRLS, and the handler then opens `tenant_scope()` for the
job's own tenant. The two are separate transactions on purpose: a long job must
not hold its claim lock open while it does I/O.
"""

from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabel_db.tenant import admin_scope

logger = logging.getLogger(__name__)

# How many jobs one worker takes per pass. Small, because a worker that claims
# fifty and then dies leaves fifty jobs locked until the lease expires.
BATCH_SIZE = 5

# A job claimed longer ago than this is assumed abandoned — the worker died
# mid-job. Generous enough that a slow-but-alive job is not stolen.
LEASE_SECONDS = 300

# Exponential, capped. A Telnyx outage should not retry a thousand recaps into
# a tight loop.
BACKOFF_SECONDS = (30, 120, 600, 1800, 3600)


@dataclass(frozen=True, slots=True)
class Job:
    id: int
    tenant_id: UUID | None
    kind: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    created_at: datetime

    @property
    def is_last_attempt(self) -> bool:
        return self.attempts + 1 >= self.max_attempts


def worker_id() -> str:
    """Who holds the lock. Host and pid, so an abandoned lease can be traced to
    a machine rather than guessed at."""
    return f"{socket.gethostname()}:{os.getpid()}"


async def claim(engine: AsyncEngine, *, limit: int = BATCH_SIZE) -> list[Job]:
    """Take up to `limit` jobs nobody else holds.

    The `WHERE` is the whole design:

    - not completed and not failed — the obvious part
    - `run_after <= now()` — respects backoff and scheduled sends
    - unlocked, or locked long enough ago to be assumed abandoned

    That last clause is what recovers from a worker dying mid-job. Without it
    a crash leaves the job locked forever and the owner never gets his recap.
    """
    async with admin_scope(reason="claim queued jobs", engine=engine) as conn:
        result = await conn.execute(
            text(
                """
                UPDATE job_queue
                SET locked_at = now(),
                    locked_by = :worker,
                    attempts = attempts + 1
                WHERE id IN (
                  SELECT id FROM job_queue
                  WHERE completed_at IS NULL
                    AND failed_at IS NULL
                    AND run_after <= now()
                    AND (locked_at IS NULL
                         OR locked_at < now() - make_interval(secs => :lease))
                  ORDER BY run_after
                  FOR UPDATE SKIP LOCKED
                  LIMIT :limit
                )
                RETURNING id, tenant_id, kind, payload, attempts, max_attempts, created_at
                """
            ),
            {"worker": worker_id(), "lease": LEASE_SECONDS, "limit": limit},
        )
        return [
            Job(
                id=row["id"],
                tenant_id=row["tenant_id"],
                kind=row["kind"],
                payload=dict(row["payload"] or {}),
                attempts=row["attempts"],
                max_attempts=row["max_attempts"],
                created_at=row["created_at"],
            )
            for row in result.mappings()
        ]


async def complete(engine: AsyncEngine, job_id: int) -> None:
    async with admin_scope(reason="mark a job complete", engine=engine) as conn:
        await conn.execute(
            text(
                "UPDATE job_queue SET completed_at = now(), locked_at = NULL, "
                "locked_by = NULL WHERE id = :id"
            ),
            {"id": job_id},
        )


async def retry_later(engine: AsyncEngine, job: Job, error: str) -> None:
    """Push the job back with exponential backoff, or fail it for good.

    The error is truncated. A stack trace in `last_error` is fine; a stack
    trace containing a customer's phone number repeated across a thousand rows
    is not.
    """
    if job.is_last_attempt:
        await fail(engine, job.id, error)
        return

    delay = BACKOFF_SECONDS[min(job.attempts - 1, len(BACKOFF_SECONDS) - 1)]
    async with admin_scope(reason="reschedule a failed job", engine=engine) as conn:
        await conn.execute(
            text(
                """
                UPDATE job_queue
                SET run_after = now() + make_interval(secs => :delay),
                    locked_at = NULL,
                    locked_by = NULL,
                    last_error = :error
                WHERE id = :id
                """
            ),
            {"id": job.id, "delay": delay, "error": error[:500]},
        )
    logger.info("job %s (%s) retrying in %ss: %s", job.id, job.kind, delay, error[:200])


async def fail(engine: AsyncEngine, job_id: int, error: str) -> None:
    """Give up. The row stays, with the reason, so somebody can look.

    Deliberately not deleted. A job that failed five times is the most
    interesting row in the table.
    """
    async with admin_scope(reason="mark a job failed", engine=engine) as conn:
        await conn.execute(
            text(
                "UPDATE job_queue SET failed_at = now(), locked_at = NULL, "
                "locked_by = NULL, last_error = :error WHERE id = :id"
            ),
            {"id": job_id, "error": error[:500]},
        )
    logger.error("job %s gave up: %s", job_id, error[:200])


async def enqueue(
    engine: AsyncEngine,
    *,
    kind: str,
    tenant_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
    run_after: datetime | None = None,
    max_attempts: int = 5,
) -> int:
    """Put a job on the queue.

    Most jobs arrive from `pg_cron` rather than through here. This is for the
    ones the application raises itself — a post-call archive, a follow-up the
    portal scheduled.
    """
    import json

    async with admin_scope(reason=f"enqueue {kind}", engine=engine) as conn:
        result = await conn.execute(
            text(
                """
                INSERT INTO job_queue (tenant_id, kind, payload, run_after, max_attempts)
                VALUES (:tenant_id, :kind, cast(:payload as jsonb),
                        coalesce(:run_after, now()), :max_attempts)
                RETURNING id
                """
            ),
            {
                "tenant_id": tenant_id,
                "kind": kind,
                "payload": json.dumps(payload or {}),
                "run_after": run_after,
                "max_attempts": max_attempts,
            },
        )
        return int(result.scalar_one())


async def depth(engine: AsyncEngine) -> dict[str, int]:
    """How much work is waiting. For the health endpoint and the 3am pager.

    A growing `ready` count with a healthy worker means jobs are failing and
    retrying; a growing count with no worker means nobody is running.
    """
    async with admin_scope(reason="queue depth", engine=engine) as conn:
        result = await conn.execute(
            text(
                """
                SELECT
                  count(*) FILTER (
                    WHERE completed_at IS NULL AND failed_at IS NULL
                      AND run_after <= now()
                      AND (locked_at IS NULL
                           OR locked_at < now() - make_interval(secs => :lease))
                  ) AS ready,
                  count(*) FILTER (
                    WHERE completed_at IS NULL AND failed_at IS NULL AND locked_at IS NOT NULL
                  ) AS in_flight,
                  count(*) FILTER (WHERE failed_at IS NOT NULL) AS failed
                FROM job_queue
                """
            ),
            {"lease": LEASE_SECONDS},
        )
        row = result.mappings().one()
        return {k: int(v) for k, v in row.items()}
