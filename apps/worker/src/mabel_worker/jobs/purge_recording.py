"""Retention: recordings older than twelve months.

**Nothing irreversible happens without a human.** AGENTS.md is explicit, and a
call recording is a contractor's evidence of what was agreed. So this job does
not delete anything. It clears our pointer to the object and records that it
did, and the actual object removal is a separate, human-initiated sweep
reconciled against this audit trail.

That makes the schema's `purge-recordings` cron a *nomination* mechanism. The
call row survives, the transcript survives, and the audio becomes unreferenced
rather than destroyed.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabel_db.queries import events as events_q
from mabel_db.tenant import tenant_scope
from mabel_worker.queue import Job

logger = logging.getLogger(__name__)


async def run(job: Job, engine: AsyncEngine) -> None:
    if job.tenant_id is None:
        raise ValueError("purge_recording needs a tenant")

    raw_call_id = job.payload.get("call_id")
    if not raw_call_id:
        raise ValueError("purge_recording needs a call_id in its payload")
    call_id = UUID(str(raw_call_id))

    async with tenant_scope(job.tenant_id, engine=engine) as conn:
        current = await conn.execute(
            text("SELECT recording_path FROM calls WHERE id = :id"), {"id": call_id}
        )
        path = current.scalar_one_or_none()
        if not path:
            # Already unlinked, or there was never a recording. Idempotent.
            return

        await conn.execute(
            text("UPDATE calls SET recording_path = NULL WHERE id = :id"), {"id": call_id}
        )

        await events_q.append(
            conn,
            tenant_id=job.tenant_id,
            kind="system",
            direction="internal",
            body="Recording passed its twelve-month retention and was unlinked.",
            payload={
                "call_id": str(call_id),
                "former_path": path,
                # Stated explicitly, because somebody reading this row later
                # needs to know the audio may still exist in the bucket.
                "object_deleted": False,
            },
        )
        logger.info("unlinked recording for call %s; object not deleted", call_id)
