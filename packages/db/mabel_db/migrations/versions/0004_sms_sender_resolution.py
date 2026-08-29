"""Resolving an inbound SMS sender to a tenant.

Revision ID: 0004_sms_sender_resolution
Revises: 0003_did_resolution
Create Date: 2026-08-29

The same shape of problem as 0003, and worth noting that it is the *second*
time: `users` is RLS-protected, and an inbound SMS arrives with only a phone
number, before any tenant context exists. A plain SELECT returns nothing.

Anywhere an inbound message from the outside world has to be attributed to a
tenant, this pattern recurs. A narrow `SECURITY DEFINER` function with a pinned
`search_path`, returning only what routing needs, and executable by `mabel_app`
alone.

**This one deliberately returns every match rather than one.** A phone number
belonging to two tenants is a real situation — an office manager who works for
two contractors — and the caller refuses to act rather than guessing which
business he meant. `LIMIT 1` here would silently file a lead under the wrong
company, so the ambiguity is surfaced instead of resolved.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_sms_sender_resolution"
down_revision: str | None = "0003_did_resolution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FUNCTION = """
CREATE OR REPLACE FUNCTION resolve_user_by_phone(p_phone text)
RETURNS TABLE (
  tenant_id  uuid,
  user_id    uuid,
  phone_e164 text,
  role       text
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT u.tenant_id, u.id, u.phone_e164, u.role
  FROM users u
  JOIN tenants t ON t.id = u.tenant_id
  WHERE u.phone_e164 = p_phone
    AND u.deleted_at IS NULL
    AND t.deleted_at IS NULL
    AND t.status IN ('trial', 'active', 'past_due');
$$;

ALTER FUNCTION resolve_user_by_phone(text) OWNER TO mabel_admin;
REVOKE ALL ON FUNCTION resolve_user_by_phone(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION resolve_user_by_phone(text) TO mabel_app;
"""


def upgrade() -> None:
    op.execute(FUNCTION)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS resolve_user_by_phone(text)")
