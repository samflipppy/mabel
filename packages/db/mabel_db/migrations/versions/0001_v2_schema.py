"""Mabel v2 schema.

Revision ID: 0001_v2_schema
Revises:
Create Date: 2026-08-29

This is 01-SCHEMA.sql, transcribed. The DDL is kept as literal SQL rather than
built through SQLAlchemy's DDL constructs so it can be read side by side with
the specification and diffed against it — `tests/unit/test_migration_matches_schema.py`
does exactly that, and fails if the two drift.

What autogenerate would have lost, and why each of these is load-bearing:

- **RLS policies.** `ENABLE ROW LEVEL SECURITY` alone is not enough — the table
  owner bypasses it. Every tenant-scoped table gets `FORCE` too.
- **Partial indexes.** `WHERE deleted_at IS NULL`, `WHERE is_live`,
  `WHERE first_touched_at IS NULL` — these are the shape of the hot queries.
- **Grants.** `mabel_app` is the application role and is not the owner.
- **`uuidv7()`.** Time-ordered primary keys, so index locality follows insert
  order. Postgres 18 has it natively; on 16/17 it is the function below.

Sam runs this. Not you.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_v2_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


EXTENSIONS = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- fuzzy contact matching
"""

# Postgres 18 has uuidv7() natively. On 16/17, this is it. Time-ordered keys
# mean new rows land at the end of the index rather than scattered through it.
UUIDV7 = """
CREATE OR REPLACE FUNCTION uuidv7() RETURNS uuid AS $$
  SELECT encode(
    set_bit(set_bit(overlay(uuid_send(gen_random_uuid())
      PLACING substring(int8send((extract(epoch FROM clock_timestamp())*1000)::bigint) FROM 3)
      FROM 1 FOR 6), 52, 1), 53, 1), 'hex')::uuid;
$$ LANGUAGE sql VOLATILE;
"""

# CREATE ROLE is not idempotent, and on a managed Postgres the role may already
# exist from the project bootstrap. The DO block makes re-running safe.
#
# mabel_app: the application connects as this. RLS applies to it.
# mabel_admin: migrations and cross-tenant analytics. Holds BYPASSRLS and is
# never used by application code — tests/isolation/ checks that.
ROLES = """
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mabel_app') THEN
    CREATE ROLE mabel_app NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mabel_admin') THEN
    CREATE ROLE mabel_admin NOLOGIN BYPASSRLS;
  END IF;
END $$;
"""

TENANCY = """
CREATE TABLE tenants (
  id                uuid PRIMARY KEY DEFAULT uuidv7(),
  business_name     text NOT NULL,
  legal_name        text,
  trade             text NOT NULL,
  timezone          text NOT NULL DEFAULT 'America/New_York',
  status            text NOT NULL DEFAULT 'trial'
                      CHECK (status IN ('trial','active','past_due','paused','churned')),
  did_e164          text UNIQUE,
  sip_registered_at timestamptz,
  xai_agent_id      text,
  stripe_customer_id text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  deleted_at        timestamptz
);

CREATE TABLE locations (
  id           uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name         text NOT NULL,
  address      text,
  did_e164     text UNIQUE,
  is_primary   boolean NOT NULL DEFAULT false,
  deleted_at   timestamptz
);
CREATE INDEX ix_locations_tenant ON locations (tenant_id) WHERE deleted_at IS NULL;

CREATE TABLE users (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id     uuid REFERENCES tenants(id) ON DELETE CASCADE,
  supabase_uid  uuid UNIQUE,
  email         citext UNIQUE NOT NULL,
  full_name     text,
  phone_e164    text,
  role          text NOT NULL DEFAULT 'office'
                  CHECK (role IN ('owner','office','tech','internal')),
  notify_emergencies boolean NOT NULL DEFAULT false,
  notify_recap       boolean NOT NULL DEFAULT false,
  created_at    timestamptz NOT NULL DEFAULT now(),
  deleted_at    timestamptz
);
CREATE INDEX ix_users_tenant ON users (tenant_id) WHERE deleted_at IS NULL;
"""

CONFIGURATION = """
CREATE TABLE agent_configs (
  id                uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  version           integer NOT NULL,
  is_live           boolean NOT NULL DEFAULT false,

  greeting          text NOT NULL,
  voice             text NOT NULL DEFAULT 'carina',
  speaking_rate     numeric(3,2) NOT NULL DEFAULT 1.0,

  services          text[] NOT NULL DEFAULT '{}',
  service_area_zips text[] NOT NULL DEFAULT '{}',
  service_area_note text,

  business_hours    jsonb NOT NULL,
  after_hours_only  boolean NOT NULL DEFAULT true,

  never_say         text[] NOT NULL DEFAULT
                      '{price,estimate_range,hourly_rate,arrival_time}',
  custom_rules      text,
  keyterms          text[] NOT NULL DEFAULT '{}',

  vertical_ruleset_id uuid,
  emergency_overrides jsonb NOT NULL DEFAULT '{}',

  created_by        uuid REFERENCES users(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  published_at      timestamptz,
  UNIQUE (tenant_id, version)
);
-- One live config per tenant, enforced by the database rather than by
-- remembering to unset the old one.
CREATE UNIQUE INDEX ix_agent_config_live
  ON agent_configs (tenant_id) WHERE is_live;

CREATE TABLE knowledge_items (
  id          uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  question    text NOT NULL,
  answer      text NOT NULL,
  sort_order  integer NOT NULL DEFAULT 0,
  is_active   boolean NOT NULL DEFAULT true,
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_knowledge_tenant ON knowledge_items (tenant_id, is_active, sort_order);

CREATE TABLE vertical_rulesets (
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  trade          text NOT NULL,
  version        integer NOT NULL,
  effective_from date NOT NULL,
  rules          jsonb NOT NULL,
  verified_by    uuid,
  verified_at    timestamptz,
  UNIQUE (trade, version)
);

CREATE TABLE oncall_schedules (
  id          uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name        text NOT NULL,
  rotation    jsonb NOT NULL,
  is_active   boolean NOT NULL DEFAULT true
);
CREATE INDEX ix_oncall_tenant ON oncall_schedules (tenant_id) WHERE is_active;
"""

CONTACTS = """
CREATE TABLE contacts (
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  display_name   text,
  primary_phone  text,
  phones         text[] NOT NULL DEFAULT '{}',
  emails         citext[] NOT NULL DEFAULT '{}',
  addresses      jsonb NOT NULL DEFAULT '[]',
  merged_into    uuid REFERENCES contacts(id),
  first_seen_at  timestamptz NOT NULL DEFAULT now(),
  last_seen_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at     timestamptz
);
CREATE INDEX ix_contacts_tenant_phone ON contacts (tenant_id, primary_phone)
  WHERE merged_into IS NULL AND deleted_at IS NULL;
CREATE INDEX ix_contacts_phones ON contacts USING gin (phones);
CREATE INDEX ix_contacts_name_trgm ON contacts USING gin (display_name gin_trgm_ops);

-- Append-only. Every interaction, one row, never updated.
CREATE TABLE communication_events (
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  contact_id     uuid REFERENCES contacts(id),
  lead_id        uuid,
  kind           text NOT NULL
                   CHECK (kind IN ('call','sms_in','sms_out','email_in','email_out',
                                   'note','estimate','photo','status_change',
                                   'identity_merged','system')),
  direction      text CHECK (direction IN ('inbound','outbound','internal')),
  occurred_at    timestamptz NOT NULL,
  body           text,
  payload        jsonb NOT NULL DEFAULT '{}',
  storage_path   text,
  actor_user_id  uuid REFERENCES users(id),
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_events_thread ON communication_events (tenant_id, contact_id, occurred_at DESC);
CREATE INDEX ix_events_lead ON communication_events (tenant_id, lead_id, occurred_at DESC);
CREATE INDEX ix_events_kind ON communication_events (tenant_id, kind, occurred_at DESC);
"""

CALLS = """
CREATE TABLE calls (
  id                uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  location_id       uuid REFERENCES locations(id),
  contact_id        uuid REFERENCES contacts(id),
  lead_id           uuid,

  xai_call_id       text UNIQUE,
  telnyx_call_id    text,
  from_e164         text,
  to_e164           text,

  started_at        timestamptz NOT NULL,
  answered_at       timestamptz,
  ended_at          timestamptz,
  duration_sec      integer,

  outcome           text CHECK (outcome IN
                      ('lead','emergency','existing_customer','spam',
                       'wrong_number','hangup','transferred','failed')),
  agent_config_id   uuid REFERENCES agent_configs(id),

  recording_path    text,
  archived_at       timestamptz,

  voice_cost_cents  integer,
  telephony_cost_cents integer,

  qa_flags          text[] NOT NULL DEFAULT '{}',
  qa_reviewed_at    timestamptz,

  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_calls_tenant_time ON calls (tenant_id, started_at DESC);
CREATE INDEX ix_calls_outcome ON calls (tenant_id, outcome, started_at DESC);
CREATE INDEX ix_calls_qa ON calls (tenant_id) WHERE array_length(qa_flags,1) > 0;

CREATE TABLE transcripts (
  id          uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  call_id     uuid NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
  turns       jsonb NOT NULL,
  full_text   text,
  tool_trace  jsonb NOT NULL DEFAULT '[]',
  summary     text,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_transcripts_call ON transcripts (call_id);
-- The feature nobody else offers: full-text search across every call a
-- contractor has ever received.
CREATE INDEX ix_transcripts_search ON transcripts
  USING gin (to_tsvector('english', coalesce(full_text,'')));
"""

LEADS = """
CREATE TABLE leads (
  id              uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  contact_id      uuid REFERENCES contacts(id),
  call_id         uuid REFERENCES calls(id),

  caller_name     text,
  service_address text,
  callback_e164   text,
  job_type        text,
  description     text,
  urgency         text NOT NULL DEFAULT 'routine'
                    CHECK (urgency IN ('routine','soon','emergency')),
  source          text,

  status          text NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new','contacted','estimate_scheduled',
                                      'estimate_sent','won','lost','spam')),
  value_cents     bigint,
  currency        text NOT NULL DEFAULT 'USD',
  lost_reason     text,

  escalated_at    timestamptz,
  first_touched_at timestamptz,
  won_at          timestamptz,

  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_leads_tenant_status ON leads (tenant_id, status, created_at DESC);
-- Drives the followup sweep and the dashboard's "needs you" list.
CREATE INDEX ix_leads_untouched ON leads (tenant_id, created_at)
  WHERE first_touched_at IS NULL AND status = 'new';

CREATE TABLE appointments (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  lead_id       uuid REFERENCES leads(id),
  contact_id    uuid REFERENCES contacts(id),
  starts_at     timestamptz NOT NULL,
  ends_at       timestamptz,
  kind          text NOT NULL DEFAULT 'estimate',
  status        text NOT NULL DEFAULT 'scheduled'
                  CHECK (status IN ('scheduled','confirmed','completed','no_show','cancelled')),
  external_ref  text,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_appts_tenant_time ON appointments (tenant_id, starts_at);
"""

NOTIFICATIONS = """
CREATE TABLE notifications (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  user_id       uuid REFERENCES users(id),
  kind          text NOT NULL
                  CHECK (kind IN ('emergency','morning_recap','weekly_summary',
                                  'followup_nudge','monthly_report','system')),
  channel       text NOT NULL CHECK (channel IN ('sms','email','push')),
  to_address    text NOT NULL,
  body          text NOT NULL,
  lead_id       uuid REFERENCES leads(id),
  status        text NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued','sent','delivered','failed')),
  provider_ref  text,
  error         text,
  scheduled_for timestamptz,
  sent_at       timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_notifications_pending ON notifications (status, scheduled_for)
  WHERE status = 'queued';
CREATE INDEX ix_notifications_tenant ON notifications (tenant_id, created_at DESC);

CREATE TABLE sms_sessions (
  id          uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  user_id     uuid REFERENCES users(id),
  phone_e164  text NOT NULL,
  context     jsonb NOT NULL DEFAULT '{}',
  expires_at  timestamptz NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ix_sms_sessions_phone ON sms_sessions (phone_e164);
"""

INTEGRATIONS = """
CREATE TABLE integrations (
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  provider       text NOT NULL
                   CHECK (provider IN ('google_calendar','jobber','housecall_pro','webhook')),
  status         text NOT NULL DEFAULT 'connected'
                   CHECK (status IN ('connected','expired','revoked','error')),
  external_account_id text,
  config         jsonb NOT NULL DEFAULT '{}',
  -- Tokens live in the Supabase vault, referenced by key, never stored here.
  vault_key      text,
  last_synced_at timestamptz,
  last_error     text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, provider)
);

CREATE TABLE integration_events (
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  integration_id uuid REFERENCES integrations(id),
  direction      text NOT NULL,
  entity         text,
  external_ref   text,
  status         text NOT NULL,
  payload        jsonb,
  created_at     timestamptz NOT NULL DEFAULT now()
);
"""

BILLING = """
CREATE TABLE subscriptions (
  id                     uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id              uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  stripe_subscription_id text UNIQUE,
  plan                   text NOT NULL CHECK (plan IN ('mabel','fulltime','plus')),
  price_cents            bigint NOT NULL,
  currency               text NOT NULL DEFAULT 'USD',
  included_minutes       integer NOT NULL,
  overage_cents_per_min  integer NOT NULL DEFAULT 0,
  addons                 jsonb NOT NULL DEFAULT '{}',
  status                 text NOT NULL,
  current_period_start   timestamptz,
  current_period_end     timestamptz,
  cancel_at              timestamptz,
  created_at             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_subs_tenant ON subscriptions (tenant_id);

CREATE TABLE usage_daily (
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  day             date NOT NULL,
  calls_answered  integer NOT NULL DEFAULT 0,
  voice_minutes   numeric(10,2) NOT NULL DEFAULT 0,
  sms_sent        integer NOT NULL DEFAULT 0,
  leads_created   integer NOT NULL DEFAULT 0,
  emergencies     integer NOT NULL DEFAULT 0,
  cost_cents      integer NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, day)
);

CREATE TABLE monthly_reports (
  id                uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  period_start      date NOT NULL,
  period_end        date NOT NULL,
  calls_answered    integer NOT NULL,
  leads_created     integer NOT NULL,
  emergencies       integer NOT NULL,
  jobs_won          integer NOT NULL,
  won_value_cents   bigint NOT NULL,
  source_breakdown  jsonb NOT NULL DEFAULT '{}',
  untouched_leads   jsonb NOT NULL DEFAULT '[]',
  pdf_path          text,
  sent_at           timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, period_start)
);
"""

QUEUE_AND_AUDIT = """
CREATE TABLE job_queue (
  id            bigserial PRIMARY KEY,
  tenant_id     uuid,
  kind          text NOT NULL,
  payload       jsonb NOT NULL DEFAULT '{}',
  run_after     timestamptz NOT NULL DEFAULT now(),
  attempts      integer NOT NULL DEFAULT 0,
  max_attempts  integer NOT NULL DEFAULT 5,
  locked_at     timestamptz,
  locked_by     text,
  completed_at  timestamptz,
  failed_at     timestamptz,
  last_error    text,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_queue_ready ON job_queue (run_after)
  WHERE completed_at IS NULL AND failed_at IS NULL;

CREATE TABLE webhook_receipts (
  webhook_id   text PRIMARY KEY,
  source       text NOT NULL,
  received_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_webhook_receipts_age ON webhook_receipts (received_at);

CREATE TABLE audit_log (
  id           bigserial PRIMARY KEY,
  tenant_id    uuid,
  actor_id     uuid,
  actor_type   text NOT NULL DEFAULT 'user',
  action       text NOT NULL,
  entity       text,
  entity_id    uuid,
  before       jsonb,
  after        jsonb,
  ip           inet,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_tenant_time ON audit_log (tenant_id, created_at DESC);
"""

# Every tenant-scoped table, in one loop, so a new table cannot be added
# without someone noticing it is missing from this list. FORCE is not optional:
# without it the table owner reads across tenants.
#
# DEVIATION from 01-SCHEMA.sql, and a necessary one. The spec writes
# `current_setting('app.tenant_id', true)::uuid`. That is correct when the
# setting is *unset* — current_setting returns NULL, the comparison is NULL,
# the policy matches nothing, and it fails closed exactly as intended. But when
# the setting is present and empty, `''::uuid` raises `invalid input syntax for
# type uuid` rather than returning NULL, so the query errors instead of
# returning zero rows. An empty value arises the moment anything resets the
# GUC, which `admin_scope()` does deliberately. nullif() collapses both cases
# onto NULL so the fail-closed behaviour is actually reachable.
RLS = """
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'locations','users','agent_configs','knowledge_items','oncall_schedules',
    'contacts','communication_events','calls','transcripts','leads',
    'appointments','notifications','sms_sessions','integrations',
    'integration_events','subscriptions','usage_daily','monthly_reports'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format($f$
      CREATE POLICY tenant_isolation ON %I
        USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
    $f$, t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO mabel_app', t);
  END LOOP;
END $$;

-- A tenant sees only its own row.
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_self ON tenants
  USING (id = nullif(current_setting('app.tenant_id', true), '')::uuid);
GRANT SELECT, UPDATE ON tenants TO mabel_app;

-- Global, not tenant-scoped.
GRANT SELECT ON vertical_rulesets TO mabel_app;
GRANT SELECT, INSERT ON audit_log TO mabel_app;
GRANT SELECT, INSERT, UPDATE ON job_queue TO mabel_app;
GRANT SELECT, INSERT ON webhook_receipts TO mabel_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO mabel_app;
"""

SECTIONS = (
    EXTENSIONS,
    UUIDV7,
    ROLES,
    TENANCY,
    CONFIGURATION,
    CONTACTS,
    CALLS,
    LEADS,
    NOTIFICATIONS,
    INTEGRATIONS,
    BILLING,
    QUEUE_AND_AUDIT,
    RLS,
)

# Reverse dependency order. Nothing here is run automatically — a downgrade on
# this revision drops every customer's call history, so it exists to be
# reviewed and refused, not to be convenient.
DROP_ORDER = (
    "monthly_reports",
    "usage_daily",
    "subscriptions",
    "integration_events",
    "integrations",
    "sms_sessions",
    "notifications",
    "appointments",
    "transcripts",
    "leads",
    "calls",
    "communication_events",
    "contacts",
    "oncall_schedules",
    "vertical_rulesets",
    "knowledge_items",
    "agent_configs",
    "users",
    "locations",
    "tenants",
    "audit_log",
    "webhook_receipts",
    "job_queue",
)


def upgrade() -> None:
    for section in SECTIONS:
        op.execute(section)


def downgrade() -> None:
    for table in DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS uuidv7()")
    # Roles are deliberately not dropped. They may own objects outside this
    # migration's knowledge, and dropping a role on a managed Postgres is the
    # kind of irreversible act that needs a human.
