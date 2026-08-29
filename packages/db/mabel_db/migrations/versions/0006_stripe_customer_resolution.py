"""Resolving a Stripe customer to a tenant.

Revision ID: 0006_stripe_customer_resolution
Revises: 0005_portal_session_resolution
Create Date: 2026-08-29

The fourth and, so far, last instance of the pattern. Worth restating once more
because by now it is clearly a shape rather than a series of accidents:

    Something arrives from outside carrying an external identifier — a dialed
    number, a phone number, a Supabase uid, a Stripe customer id. The table
    that knows which tenant it belongs to has RLS forced on it. There is no
    tenant context yet, because *which tenant it is* is the question. A plain
    SELECT therefore returns nothing, however correct the SQL looks.

The answer, four times now: a narrow `SECURITY DEFINER` function with a pinned
`search_path`, returning only what routing needs, executable by `mabel_app`
alone. Not a BYPASSRLS connection, which would hand a whole process
unrestricted read for its lifetime to solve one lookup.

If you are adding a fifth — a Jobber account id, a Google Calendar channel —
this is the file to copy.

`tenants.stripe_customer_id` has no unique constraint in 01-SCHEMA.sql, so this
returns at most one row explicitly. Two tenants sharing a Stripe customer would
be a data error rather than a legitimate ambiguity, and billing the wrong one
is worse than billing neither.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_stripe_customer_resolution"
down_revision: str | None = "0005_portal_session_resolution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FUNCTION = """
CREATE OR REPLACE FUNCTION resolve_tenant_by_stripe_customer(p_customer_id text)
RETURNS TABLE (tenant_id uuid, status text)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT t.id, t.status
  FROM tenants t
  WHERE t.stripe_customer_id = p_customer_id
    AND t.deleted_at IS NULL
  LIMIT 1;
$$;

ALTER FUNCTION resolve_tenant_by_stripe_customer(text) OWNER TO mabel_admin;
REVOKE ALL ON FUNCTION resolve_tenant_by_stripe_customer(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION resolve_tenant_by_stripe_customer(text) TO mabel_app;

-- A Stripe customer belongs to exactly one tenant. Without this, a copy-paste
-- during onboarding silently points two businesses at one subscription and the
-- lookup above starts returning whichever row Postgres felt like.
CREATE UNIQUE INDEX IF NOT EXISTS ix_tenants_stripe_customer
  ON tenants (stripe_customer_id)
  WHERE stripe_customer_id IS NOT NULL AND deleted_at IS NULL;
"""


def upgrade() -> None:
    op.execute(FUNCTION)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_tenants_stripe_customer")
    op.execute("DROP FUNCTION IF EXISTS resolve_tenant_by_stripe_customer(text)")
