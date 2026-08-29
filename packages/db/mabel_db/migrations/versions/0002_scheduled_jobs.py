"""pg_cron schedules that fill the job queue.

Revision ID: 0002_scheduled_jobs
Revises: 0001_v2_schema
Create Date: 2026-08-29

Split from 0001 on purpose. `pg_cron` is a superuser extension and is not
available on every Postgres — a local Docker instance or an ephemeral CI
database will not have it. Keeping it in its own revision means the schema and
the RLS isolation tests can run anywhere, and only this revision needs the
managed Supabase project.

Every entry here does the same small thing: insert rows into `job_queue`. Cron
never does the work. The worker does, through `tenant_scope()`, where RLS
applies. A cron job that sent an SMS directly would be a scheduled task running
as a superuser with no tenant context, which is precisely what invariant 2
exists to prevent.

`cron.schedule` upserts by name, so re-running this revision is safe.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_scheduled_jobs"
down_revision: str | None = "0001_v2_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


EXTENSION = "CREATE EXTENSION IF NOT EXISTS pg_cron;"

# Named so `cron.unschedule` in downgrade() has something to aim at.
JOBS: tuple[tuple[str, str, str], ...] = (
    (
        "enqueue-recaps",
        "0 * * * *",
        # Runs hourly and picks off whichever tenants have just hit 7am local.
        # Hourly-and-filter rather than one cron per timezone, because the
        # timezone is a tenant column and can change.
        """
        INSERT INTO job_queue (tenant_id, kind, payload)
        SELECT id, 'morning_recap', '{}'::jsonb
        FROM tenants
        WHERE status IN ('trial','active')
          AND extract(hour FROM now() AT TIME ZONE timezone) = 7;
        """,
    ),
    (
        "followup-sweep",
        "15 * * * *",
        # A lead nobody has touched in 24 hours. Capped at 7 days so we nudge
        # about a live lead, not about one the owner has clearly written off.
        """
        INSERT INTO job_queue (tenant_id, kind, payload)
        SELECT DISTINCT tenant_id, 'followup_nudge', jsonb_build_object('lead_id', id)
        FROM leads
        WHERE first_touched_at IS NULL
          AND status = 'new'
          AND created_at < now() - interval '24 hours'
          AND created_at > now() - interval '7 days';
        """,
    ),
    (
        "silence-watch",
        "0 14 * * *",
        # The silent-failure churn catcher. A tenant who used to get calls and
        # now gets none has almost certainly broken their call forwarding, and
        # they will blame us before they check. Requires prior traffic, so a
        # brand new tenant does not trip it.
        """
        INSERT INTO job_queue (tenant_id, kind, payload)
        SELECT t.id, 'silence_alert', '{}'::jsonb
        FROM tenants t
        WHERE t.status = 'active'
          AND NOT EXISTS (
            SELECT 1 FROM calls c
            WHERE c.tenant_id = t.id AND c.started_at > now() - interval '7 days')
          AND EXISTS (
            SELECT 1 FROM calls c
            WHERE c.tenant_id = t.id AND c.started_at > now() - interval '30 days');
        """,
    ),
    (
        "monthly-reports",
        "0 8 1 * *",
        """
        INSERT INTO job_queue (tenant_id, kind, payload)
        SELECT id, 'monthly_report', '{}'::jsonb
        FROM tenants WHERE status IN ('trial','active');
        """,
    ),
    (
        "purge-recordings",
        "0 3 * * 0",
        # Enqueues, never deletes. Invariant: nothing irreversible happens
        # without a human, and the worker job is where that check lives.
        """
        INSERT INTO job_queue (tenant_id, kind, payload)
        SELECT tenant_id, 'purge_recording', jsonb_build_object('call_id', id)
        FROM calls
        WHERE recording_path IS NOT NULL
          AND started_at < now() - interval '12 months';
        """,
    ),
    (
        "prune-webhook-receipts",
        "*/10 * * * *",
        # Idempotency keys are held 10 minutes, matching the webhook timestamp
        # tolerance. This is the one cron entry that deletes, and what it
        # deletes is a hash with no customer data in it.
        "DELETE FROM webhook_receipts WHERE received_at < now() - interval '10 minutes';",
    ),
)


def upgrade() -> None:
    op.execute(EXTENSION)
    for name, schedule, body in JOBS:
        statement = body.strip().replace("'", "''")
        op.execute(f"SELECT cron.schedule('{name}', '{schedule}', '{statement}');")


def downgrade() -> None:
    for name, _schedule, _body in JOBS:
        op.execute(f"SELECT cron.unschedule('{name}');")
