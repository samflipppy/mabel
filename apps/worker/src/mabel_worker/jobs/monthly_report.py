"""The monthly report row. Phase 5 renders the PDF from it.

The handler lands in Phase 3 rather than Phase 5 because the cron entry that
enqueues it is already in the schema, and a queued job with no handler fails
loudly on the first of every month.

**Every figure is a count or a sum of integer cents.** `won_value_cents` sums
values the owner typed in himself. Nothing here is estimated, inferred, or
produced by a model — 02-PORTAL.md's report works as a retention artifact
precisely because the owner recognises his own numbers in it.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabel_db.tenant import tenant_scope
from mabel_worker.queue import Job

logger = logging.getLogger(__name__)


def previous_month(today: date) -> tuple[date, date]:
    """The month that just ended. The cron fires on the 1st at 8am."""
    first_of_this = today.replace(day=1)
    last_of_previous = first_of_this - timedelta(days=1)
    return last_of_previous.replace(day=1), last_of_previous


async def run(job: Job, engine: AsyncEngine, *, today: date | None = None) -> None:
    if job.tenant_id is None:
        raise ValueError("monthly_report needs a tenant")

    start, end = previous_month(today or date.today())

    async with tenant_scope(job.tenant_id, engine=engine) as conn:
        figures = await conn.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM calls
                     WHERE started_at::date BETWEEN :start AND :end) AS calls_answered,
                  (SELECT count(*) FROM leads
                     WHERE created_at::date BETWEEN :start AND :end) AS leads_created,
                  (SELECT count(*) FROM calls
                     WHERE started_at::date BETWEEN :start AND :end
                       AND outcome = 'emergency') AS emergencies,
                  (SELECT count(*) FROM leads
                     WHERE won_at::date BETWEEN :start AND :end) AS jobs_won,
                  (SELECT coalesce(sum(value_cents), 0) FROM leads
                     WHERE won_at::date BETWEEN :start AND :end) AS won_value_cents
                """
            ),
            {"start": start, "end": end},
        )
        row = figures.mappings().one()

        sources = await conn.execute(
            text(
                """
                SELECT coalesce(source, 'unknown') AS source, count(*) AS n
                FROM leads
                WHERE created_at::date BETWEEN :start AND :end
                GROUP BY 1
                ORDER BY 2 DESC
                """
            ),
            {"start": start, "end": end},
        )
        breakdown = {r["source"]: int(r["n"]) for r in sources.mappings()}

        # "Still waiting on you: 2 leads, oldest 9 days." The report is not
        # allowed to be only good news.
        untouched = await conn.execute(
            text(
                """
                SELECT caller_name, job_type, created_at
                FROM leads
                WHERE first_touched_at IS NULL
                  AND status = 'new'
                  AND created_at::date <= :end
                ORDER BY created_at
                LIMIT 20
                """
            ),
            {"end": end},
        )
        waiting = [
            {
                "name": r["caller_name"],
                "job_type": r["job_type"],
                "since": r["created_at"].date().isoformat(),
            }
            for r in untouched.mappings()
        ]

        await conn.execute(
            text(
                """
                INSERT INTO monthly_reports
                  (tenant_id, period_start, period_end, calls_answered, leads_created,
                   emergencies, jobs_won, won_value_cents, source_breakdown, untouched_leads)
                VALUES
                  (:tenant_id, :start, :end, :calls, :leads, :emergencies, :won,
                   :value, cast(:sources as jsonb), cast(:waiting as jsonb))
                ON CONFLICT (tenant_id, period_start) DO UPDATE SET
                  calls_answered = excluded.calls_answered,
                  leads_created = excluded.leads_created,
                  emergencies = excluded.emergencies,
                  jobs_won = excluded.jobs_won,
                  won_value_cents = excluded.won_value_cents,
                  source_breakdown = excluded.source_breakdown,
                  untouched_leads = excluded.untouched_leads
                """
            ),
            {
                "tenant_id": job.tenant_id,
                "start": start,
                "end": end,
                "calls": int(row["calls_answered"]),
                "leads": int(row["leads_created"]),
                "emergencies": int(row["emergencies"]),
                "won": int(row["jobs_won"]),
                "value": int(row["won_value_cents"]),
                "sources": json.dumps(breakdown),
                "waiting": json.dumps(waiting),
            },
        )
