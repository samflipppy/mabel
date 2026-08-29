"""Resolving the dialed number to a tenant, before a tenant exists.

Revision ID: 0003_did_resolution
Revises: 0002_scheduled_jobs
Create Date: 2026-08-29

**This closes a gap in 01-SCHEMA.sql.** Raise it with Sam rather than assuming
it was intentional.

The problem. 03-VOICE.md requires that the tenant is resolved server-side from
the dialed number, before the session opens — that is invariant 3, and the
whole tenant-security design rests on it. The resolution is a lookup in
`tenants.did_e164`.

But `tenants` has RLS forced on it, with a policy of
`id = current_setting('app.tenant_id')`. At the moment we need to do this
lookup there *is* no `app.tenant_id`, because which tenant it is is precisely
the question being asked. So the policy matches zero rows and the lookup
returns nothing. As specified, no inbound call can ever be routed.

Three ways out, and why this one:

1. **Connect as `mabel_admin`.** It holds BYPASSRLS. But AGENTS.md says that
   role is for migrations and cross-tenant analytics and is never used by
   application code, and the call path is very much application code. Handing
   the media process a BYPASSRLS connection to solve one lookup gives it
   unrestricted read of every tenant for the life of the process.

2. **A separate un-scoped DID directory table.** Works, but adds a table that
   is not in the schema and a second source of truth for a number, which then
   has to be kept in step with `tenants` and `locations` by trigger.

3. **A `SECURITY DEFINER` function.** What is below. It runs as its owner, so
   it sees past RLS, but it is a single function with a fixed body that
   accepts one phone number and returns one tenant id and its routing facts.
   `mabel_app` gets EXECUTE on it and nothing else. The blast radius is exactly
   the lookup, rather than a whole connection.

`search_path` is pinned inside the function. A `SECURITY DEFINER` function
without that is the classic Postgres privilege-escalation hole: a caller sets
`search_path` to a schema they control, and the function resolves `tenants` to
their table instead of ours.

The function returns routing facts only — no call history, no leads, nothing a
caller could mine by dialling numbers at random to enumerate customers.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_did_resolution"
down_revision: str | None = "0002_scheduled_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FUNCTION = """
CREATE OR REPLACE FUNCTION resolve_tenant_by_did(p_did text)
RETURNS TABLE (
  tenant_id     uuid,
  location_id   uuid,
  business_name text,
  trade         text,
  timezone      text,
  status        text,
  xai_agent_id  text
)
LANGUAGE sql
STABLE
SECURITY DEFINER
-- Pinned. Without this a caller can point `tenants` at a table they control.
SET search_path = public, pg_temp
AS $$
  SELECT t.id, l.id, t.business_name, t.trade, t.timezone, t.status, t.xai_agent_id
  FROM tenants t
  LEFT JOIN locations l
    ON l.tenant_id = t.id AND l.did_e164 = p_did AND l.deleted_at IS NULL
  WHERE t.deleted_at IS NULL
    AND (t.did_e164 = p_did OR l.id IS NOT NULL)
  LIMIT 1;
$$;

-- Owned by the role that can see past RLS, executable by the application.
ALTER FUNCTION resolve_tenant_by_did(text) OWNER TO mabel_admin;
REVOKE ALL ON FUNCTION resolve_tenant_by_did(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION resolve_tenant_by_did(text) TO mabel_app;
"""


def upgrade() -> None:
    op.execute(FUNCTION)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS resolve_tenant_by_did(text)")
