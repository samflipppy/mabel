"""Re-run the QA pass over an archived call.

Normally QA runs inside `postcall.finalize`. This exists for the cases where it
did not — a call archived before its trade had a ruleset, or one archived while
the QA module was mid-deploy — and for re-checking calls after a ruleset
changes, which is the more useful reason: a rule that was wrong last month
should be able to surface the calls it got wrong.

Idempotent. It recomputes the flags and overwrites, so running it twice leaves
the same result.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabel_db.tenant import tenant_scope
from mabel_media.qa import QaInputs, assistant_text_from_turns, review
from mabel_verticals.engine import classify
from mabel_verticals.loader import load_latest
from mabel_worker.queue import Job

logger = logging.getLogger(__name__)


async def run(job: Job, engine: AsyncEngine) -> None:
    if job.tenant_id is None:
        raise ValueError("qa_review needs a tenant")

    raw_call_id = job.payload.get("call_id")
    if not raw_call_id:
        raise ValueError("qa_review needs a call_id in its payload")
    call_id = UUID(str(raw_call_id))

    async with tenant_scope(job.tenant_id, engine=engine) as conn:
        found = await conn.execute(
            text(
                """
                SELECT c.started_at, c.duration_sec, c.outcome, t.turns,
                       tn.timezone, tn.trade
                FROM calls c
                JOIN transcripts t ON t.call_id = c.id
                CROSS JOIN tenants tn
                WHERE c.id = :id
                """
            ),
            {"id": call_id},
        )
        row = found.mappings().one_or_none()
        if row is None:
            # No transcript, so nothing to review. Not an error: a call that
            # failed before anyone spoke has no turns.
            return

        turns = list(row["turns"] or [])

        backstop = False
        try:
            ruleset = load_latest(row["trade"])
        except FileNotFoundError:
            ruleset = None
        if ruleset is not None:
            scenario = {
                "utterances": [
                    str(turn.get("text", ""))
                    for turn in turns
                    if str(turn.get("role", "")).lower() not in {"assistant", "mabel"}
                ],
                "captured": {},
                "context": {},
            }
            backstop = classify(ruleset, scenario).escalate

        flags = review(
            QaInputs(
                duration_sec=int(row["duration_sec"] or 0),
                started_at=row["started_at"],
                timezone=row["timezone"],
                assistant_text=assistant_text_from_turns(turns),
                backstop_escalates=backstop,
                escalated=row["outcome"] == "emergency",
                # Not recoverable from the archive, and defaulting to False
                # only makes the arrival-time check stricter, which is the safe
                # direction for a re-run.
                booked_a_slot=False,
            )
        )

        await conn.execute(
            text("UPDATE calls SET qa_flags = :flags, qa_reviewed_at = now() WHERE id = :id"),
            {"id": call_id, "flags": flags},
        )
