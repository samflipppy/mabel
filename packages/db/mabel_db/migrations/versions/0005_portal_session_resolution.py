"""Resolving a Supabase session to a tenant.

Revision ID: 0005_portal_session_resolution
Revises: 0004_sms_sender_resolution
Create Date: 2026-08-29

The third instance of the same pattern, which makes it a pattern rather than a
series of one-offs. Worth stating plainly for whoever adds the fourth:

**Anything arriving from outside has to be attributed to a tenant before tenant
context exists.** A dialed number (0003), an SMS sender (0004), a Supabase uid
(this one). In every case the table holding the answer is RLS-protected, so a
plain SELECT returns nothing however correct the SQL looks.

The answer each time is the same: a narrow `SECURITY DEFINER` function with a
pinned `search_path`, returning only what routing needs, executable by
`mabel_app` and nobody else. Not a BYPASSRLS connection, which would hand a
whole process unrestricted read for the life of the process.

This one returns at most one row. A Supabase uid is unique on `users` by the
schema's own constraint, so unlike the phone-number case there is no ambiguity
to surface.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_portal_session_resolution"
down_revision: str | None = "0004_sms_sender_resolution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FUNCTION = """
CREATE OR REPLACE FUNCTION resolve_user_by_supabase_uid(p_uid uuid)
RETURNS TABLE (
  user_id   uuid,
  tenant_id uuid,
  email     text,
  role      text
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT u.id, u.tenant_id, u.email::text, u.role
  FROM users u
  JOIN tenants t ON t.id = u.tenant_id
  WHERE u.supabase_uid = p_uid
    AND u.deleted_at IS NULL
    AND t.deleted_at IS NULL
    -- A churned tenant cannot sign in. A past_due one can: locking somebody
    -- out of their own call history over a failed card is how a billing
    -- problem becomes a support problem.
    AND t.status IN ('trial', 'active', 'past_due', 'paused')
  LIMIT 1;
$$;

ALTER FUNCTION resolve_user_by_supabase_uid(uuid) OWNER TO mabel_admin;
REVOKE ALL ON FUNCTION resolve_user_by_supabase_uid(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION resolve_user_by_supabase_uid(uuid) TO mabel_app;
"""


def upgrade() -> None:
    op.execute(FUNCTION)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS resolve_user_by_supabase_uid(uuid)")
