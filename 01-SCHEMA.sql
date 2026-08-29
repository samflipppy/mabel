-- Mabel v2 — schema
-- Postgres 16+ / Supabase
--
-- Every tenant-scoped table carries tenant_id, has RLS ENABLED and FORCED,
-- and leads its hot indexes with tenant_id.
--
-- The app connects as a non-owner role and runs every query inside a
-- transaction beginning with:  SET LOCAL app.tenant_id = '<uuid>';
-- If unset, current_setting(..., true) returns NULL and policies match zero
-- rows. Fails closed.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_cron;
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- fuzzy contact matching

-- Postgres 18 has uuidv7() natively. On 16/17, use this.
CREATE OR REPLACE FUNCTION uuidv7() RETURNS uuid AS $$
  SELECT encode(
    set_bit(set_bit(overlay(uuid_send(gen_random_uuid())
      PLACING substring(int8send((extract(epoch FROM clock_timestamp())*1000)::bigint) FROM 3)
      FROM 1 FOR 6), 52, 1), 53, 1), 'hex')::uuid;
$$ LANGUAGE sql VOLATILE;

CREATE ROLE mabel_app NOLOGIN;      -- the application role; RLS applies
CREATE ROLE mabel_admin NOLOGIN BYPASSRLS;  -- migrations + cross-tenant analytics only


-- ============================================================
-- TENANCY
-- ============================================================

CREATE TABLE tenants (
  id                uuid PRIMARY KEY DEFAULT uuidv7(),
  business_name     text NOT NULL,
  legal_name        text,
  trade             text NOT NULL,            -- plumbing|hvac|electrical|restoration|...
  timezone          text NOT NULL DEFAULT 'America/New_York',  -- IANA
  status            text NOT NULL DEFAULT 'trial'
                      CHECK (status IN ('trial','active','past_due','paused','churned')),
  did_e164          text UNIQUE,              -- the Mabel number they forward to
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
  did_e164     text UNIQUE,                   -- multi-location tenants get their own numbers
  is_primary   boolean NOT NULL DEFAULT false,
  deleted_at   timestamptz
);
CREATE INDEX ix_locations_tenant ON locations (tenant_id) WHERE deleted_at IS NULL;

CREATE TABLE users (
  id            uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id     uuid REFERENCES tenants(id) ON DELETE CASCADE,  -- NULL = internal staff
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


-- ============================================================
-- MABEL CONFIGURATION (what the portal edits)
-- ============================================================

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

  business_hours    jsonb NOT NULL,     -- {mon:{open:"08:00",close:"17:00"},...}
  after_hours_only  boolean NOT NULL DEFAULT true,

  never_say         text[] NOT NULL DEFAULT
                      '{price,estimate_range,hourly_rate,arrival_time}',
  custom_rules      text,               -- free text appended to the prompt
  keyterms          text[] NOT NULL DEFAULT '{}',  -- street names, brands

  vertical_ruleset_id uuid,             -- FK to vertical_rulesets
  emergency_overrides jsonb NOT NULL DEFAULT '{}',

  created_by        uuid REFERENCES users(id),
  created_at        timestamptz NOT NULL DEFAULT now(),
  published_at      timestamptz,
  UNIQUE (tenant_id, version)
);
CREATE UNIQUE INDEX ix_agent_config_live
  ON agent_configs (tenant_id) WHERE is_live;

-- Q&A Mabel can answer. Editable in the portal.
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

-- Per-trade emergency rules. Global library, tenant may override.
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

-- Who gets woken up, in what order.
CREATE TABLE oncall_schedules (
  id          uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name        text NOT NULL,
  rotation    jsonb NOT NULL,    -- [{user_id, days:[1,2], start:"17:00", end:"08:00"}]
  is_active   boolean NOT NULL DEFAULT true
);
CREATE INDEX ix_oncall_tenant ON oncall_schedules (tenant_id) WHERE is_active;


-- ============================================================
-- CONTACTS & THE COMMUNICATION THREAD
-- ============================================================

CREATE TABLE contacts (
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  display_name   text,
  primary_phone  text,
  phones         text[] NOT NULL DEFAULT '{}',
  emails         citext[] NOT NULL DEFAULT '{}',
  addresses      jsonb NOT NULL DEFAULT '[]',
  merged_into    uuid REFERENCES contacts(id),   -- NULL unless merged away
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


-- ============================================================
-- CALLS
-- ============================================================

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

  -- cost tracking, integer cents
  voice_cost_cents  integer,
  telephony_cost_cents integer,

  -- QA
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
  turns       jsonb NOT NULL,      -- [{role,text,started_ms,ended_ms}]
  full_text   text,
  tool_trace  jsonb NOT NULL DEFAULT '[]',
  summary     text,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_transcripts_call ON transcripts (call_id);
CREATE INDEX ix_transcripts_search ON transcripts
  USING gin (to_tsvector('english', coalesce(full_text,'')));


-- ============================================================
-- LEADS & JOBS
-- ============================================================

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
  source          text,                 -- how they heard about the business

  status          text NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new','contacted','estimate_scheduled',
                                      'estimate_sent','won','lost','spam')),
  value_cents     bigint,               -- owner-entered. Never computed.
  currency        text NOT NULL DEFAULT 'USD',
  lost_reason     text,

  escalated_at    timestamptz,
  first_touched_at timestamptz,
  won_at          timestamptz,

  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_leads_tenant_status ON leads (tenant_id, status, created_at DESC);
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
  external_ref  text,             -- Google Calendar / Jobber id
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_appts_tenant_time ON appointments (tenant_id, starts_at);


-- ============================================================
-- NOTIFICATIONS
-- ============================================================

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

-- Owner SMS conversation state (for "reply 1", "WON RUIZ 3800")
CREATE TABLE sms_sessions (
  id          uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  user_id     uuid REFERENCES users(id),
  phone_e164  text NOT NULL,
  context     jsonb NOT NULL DEFAULT '{}',   -- last list shown, pending prompt
  expires_at  timestamptz NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ix_sms_sessions_phone ON sms_sessions (phone_e164);


-- ============================================================
-- INTEGRATIONS
-- ============================================================

CREATE TABLE integrations (
  id             uuid PRIMARY KEY DEFAULT uuidv7(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  provider       text NOT NULL
                   CHECK (provider IN ('google_calendar','jobber','housecall_pro','webhook')),
  status         text NOT NULL DEFAULT 'connected'
                   CHECK (status IN ('connected','expired','revoked','error')),
  external_account_id text,
  config         jsonb NOT NULL DEFAULT '{}',
  -- tokens live in Supabase vault, referenced by key, never stored here
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


-- ============================================================
-- BILLING & USAGE
-- ============================================================

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


-- ============================================================
-- JOB QUEUE & AUDIT
-- ============================================================

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
  actor_type   text NOT NULL DEFAULT 'user',   -- user|system|agent|internal
  action       text NOT NULL,
  entity       text,
  entity_id    uuid,
  before       jsonb,
  after        jsonb,
  ip           inet,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_tenant_time ON audit_log (tenant_id, created_at DESC);


-- ============================================================
-- ROW LEVEL SECURITY
-- ============================================================

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
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
    $f$, t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO mabel_app', t);
  END LOOP;
END $$;

-- tenants: a tenant sees only its own row
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_self ON tenants
  USING (id = current_setting('app.tenant_id', true)::uuid);
GRANT SELECT, UPDATE ON tenants TO mabel_app;

-- Global, not tenant-scoped
GRANT SELECT ON vertical_rulesets TO mabel_app;
GRANT SELECT, INSERT ON audit_log TO mabel_app;
GRANT SELECT, INSERT, UPDATE ON job_queue TO mabel_app;
GRANT SELECT, INSERT ON webhook_receipts TO mabel_app;


-- ============================================================
-- SCHEDULED JOBS
-- ============================================================

-- Morning recaps: enqueue per tenant at 7am local
SELECT cron.schedule('enqueue-recaps', '0 * * * *', $$
  INSERT INTO job_queue (tenant_id, kind, payload)
  SELECT id, 'morning_recap', '{}'::jsonb
  FROM tenants
  WHERE status IN ('trial','active')
    AND extract(hour FROM now() AT TIME ZONE timezone) = 7;
$$);

-- Untouched lead nudges, hourly
SELECT cron.schedule('followup-sweep', '15 * * * *', $$
  INSERT INTO job_queue (tenant_id, kind, payload)
  SELECT DISTINCT tenant_id, 'followup_nudge', jsonb_build_object('lead_id', id)
  FROM leads
  WHERE first_touched_at IS NULL
    AND status = 'new'
    AND created_at < now() - interval '24 hours'
    AND created_at > now() - interval '7 days';
$$);

-- Silent-failure watch: tenants that went quiet
SELECT cron.schedule('silence-watch', '0 14 * * *', $$
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
$$);

-- Monthly reports on the 1st
SELECT cron.schedule('monthly-reports', '0 8 1 * *', $$
  INSERT INTO job_queue (tenant_id, kind, payload)
  SELECT id, 'monthly_report', '{}'::jsonb
  FROM tenants WHERE status IN ('trial','active');
$$);

-- Retention: purge recordings older than 12 months
SELECT cron.schedule('purge-recordings', '0 3 * * 0', $$
  INSERT INTO job_queue (tenant_id, kind, payload)
  SELECT tenant_id, 'purge_recording', jsonb_build_object('call_id', id)
  FROM calls
  WHERE recording_path IS NOT NULL
    AND started_at < now() - interval '12 months';
$$);

-- Webhook idempotency keys expire
SELECT cron.schedule('prune-webhook-receipts', '*/10 * * * *', $$
  DELETE FROM webhook_receipts WHERE received_at < now() - interval '10 minutes';
$$);
