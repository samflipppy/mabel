"""Remembering which call legs were answered, so a hangup can be judged.

Revision ID: 0008_call_legs
Revises: 0007_customer_messaging
Create Date: 2026-08-29

The missed-call text goes out on a hangup for a leg we never saw answered, so
something has to hold "this leg was answered" between the two events.

**Not `webhook_receipts`.** That was the first attempt and it is wrong in a way
worth writing down. It is pruned every ten minutes, which is correct for
idempotency keys and catastrophic here: a call lasting eleven minutes would
have its answer marker pruned before the hangup arrived, the hangup would look
like a missed call, and a customer who had just spent eleven minutes talking to
Mabel would be texted "sorry we missed your call". The bug would appear only on
long calls, which are the good ones.

So: a table of its own, with retention measured against how long a phone call
can last rather than how long a webhook retries. Twenty-four hours is far more
than either.

Not tenant-scoped, and deliberately so. A leg id arrives before any tenant is
resolved -- the same reason `webhook_receipts` is not scoped either. It holds
an opaque provider id and a timestamp, and nothing else; there is no customer
data here to isolate.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_call_legs"
down_revision: str | None = "0007_customer_messaging"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE = """
CREATE TABLE IF NOT EXISTS call_legs (
  leg_id      text PRIMARY KEY,
  answered_at timestamptz,
  seen_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_call_legs_seen ON call_legs (seen_at);

GRANT SELECT, INSERT, UPDATE ON call_legs TO mabel_app;
"""


def upgrade() -> None:
    op.execute(TABLE)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS call_legs")
