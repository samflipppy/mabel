"""Consent, opt-out, and the settings that gate customer-facing SMS.

Revision ID: 0007_customer_messaging
Revises: 0006_stripe_customer_resolution
Create Date: 2026-08-29

Up to here every message Mabel sends goes to the owner. Owners are users of the
product: they signed up, they agreed to be texted, and the only compliance
surface is STOP. Texting the *caller* is a different legal object. They never
signed anything. What they did was phone a business, which is implied consent
for a reply about that call and nothing else.

So this revision records three things rather than assuming any of them:

`sms_consent_at` -- when the contact called us, which is the moment consent
begins. Null means we have never spoken and must not text them.

`sms_opt_out_at` -- STOP. Checked before every send, forever, and never
cleared by anything automatic.

`customer_sms_enabled` -- the tenant's own switch, defaulting to **false**.
A tenant who has not registered a 10DLC campaign for this traffic (BLOCKED #4)
would otherwise have messages accepted by the API and dropped by the carrier,
and would never know. Off by default means turning it on is a deliberate act
taken after the campaign exists.

`review_url` is nullable with no default because there is no sensible default:
a review request pointing at the wrong business is worse than none.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_customer_messaging"
down_revision: str | None = "0006_stripe_customer_resolution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


COLUMNS = """
ALTER TABLE contacts
  ADD COLUMN IF NOT EXISTS sms_consent_at  timestamptz,
  ADD COLUMN IF NOT EXISTS sms_opt_out_at  timestamptz;

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS customer_sms_enabled   boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS review_requests_enabled boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS review_url             text;

-- Suppression is checked on the send path of every customer message, so it
-- wants to be an index lookup rather than a scan. Partial, because the rows
-- that matter are the small minority who opted out.
CREATE INDEX IF NOT EXISTS ix_contacts_opted_out
  ON contacts (tenant_id, primary_phone) WHERE sms_opt_out_at IS NOT NULL;

-- Four new notification kinds, all prefixed `customer_`. The prefix is load
-- bearing rather than decorative: it is what lets the suppression audit, the
-- delivery-receipt handler and the usage rollup separate messages sent to a
-- paying owner from messages sent to a member of the public, who is the only
-- one of the two whose carrier complaint can end the campaign.
ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_kind_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check
  CHECK (kind IN ('emergency','morning_recap','weekly_summary',
                  'followup_nudge','monthly_report','system',
                  'customer_confirmation','customer_missed_call',
                  'customer_emergency','customer_review'));
"""


# A customer's number is not unique across tenants -- one homeowner may use a
# plumber and a roofer who both run Mabel -- so unlike `resolve_tenant_by_did`
# this returns every match and the caller acts on all of them.
#
# That is the correct shape for the only thing it is used for. STOP arriving
# from a number we hold in two tenants means stop, in both. Picking one and
# guessing would leave a person who has asked to be left alone still receiving
# messages, which is the failure that carries a fine.
FUNCTION = """
CREATE OR REPLACE FUNCTION resolve_contacts_by_phone(p_phone text)
RETURNS TABLE (
  tenant_id  uuid,
  contact_id uuid
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT c.tenant_id, c.id
  FROM contacts c
  JOIN tenants t ON t.id = c.tenant_id
  WHERE (c.primary_phone = p_phone OR p_phone = ANY(c.phones))
    AND c.deleted_at IS NULL
    AND c.merged_into IS NULL
    AND t.deleted_at IS NULL;
$$;

-- The owner of a SECURITY DEFINER function executes it with its own
-- privileges, and `mabel_admin` was granted SELECT on tenants, locations and
-- users in 0001 because those were the only tables a resolver read. This one
-- reads `contacts`, so the grant has to grow with it.
--
-- BYPASSRLS does not cover this. It gets past row policies; it is not a table
-- privilege, and without the line below every call fails with "permission
-- denied for table contacts". That is the third time this exact confusion has
-- cost an hour on this codebase, which is why it is written down here rather
-- than folded into 0001.
GRANT SELECT ON contacts TO mabel_admin;

ALTER FUNCTION resolve_contacts_by_phone(text) OWNER TO mabel_admin;
REVOKE ALL ON FUNCTION resolve_contacts_by_phone(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION resolve_contacts_by_phone(text) TO mabel_app;
"""


def upgrade() -> None:
    op.execute(COLUMNS)
    op.execute(FUNCTION)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS resolve_contacts_by_phone(text)")
    op.execute(
        "ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_kind_check; "
        "ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check "
        "CHECK (kind IN ('emergency','morning_recap','weekly_summary',"
        "'followup_nudge','monthly_report','system'))"
    )
    op.execute("DROP INDEX IF EXISTS ix_contacts_opted_out")
    op.execute(
        "ALTER TABLE tenants "
        "DROP COLUMN IF EXISTS customer_sms_enabled, "
        "DROP COLUMN IF EXISTS review_requests_enabled, "
        "DROP COLUMN IF EXISTS review_url"
    )
    op.execute(
        "ALTER TABLE contacts "
        "DROP COLUMN IF EXISTS sms_consent_at, "
        "DROP COLUMN IF EXISTS sms_opt_out_at"
    )
