-- Draft only. Sam runs this. A bot does not.
-- Call archives (our copy, not xAI's cache), recap queue persist, lead SMS
-- flags for the overnight page, and zip retire so the owner can replace a
-- service-area list. App role: no DELETE on tenants, leads, notes, or DIDs.
-- Zip rows are retired, not deleted. Nothing irreversible without a human.

BEGIN;

ALTER TABLE leads
    ADD COLUMN sms_sent boolean,
    ADD COLUMN sms_reason text;

ALTER TABLE service_area_zips
    ADD COLUMN retired_at timestamptz;

CREATE TABLE call_archives (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants (id),
    call_id text NOT NULL,
    transcript text NOT NULL,
    recording_uri text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE recap_queue (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants (id),
    recap_at timestamptz NOT NULL,
    lead_id uuid REFERENCES leads (id),
    sent_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE call_archives ENABLE ROW LEVEL SECURITY;
ALTER TABLE call_archives FORCE ROW LEVEL SECURITY;
ALTER TABLE recap_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE recap_queue FORCE ROW LEVEL SECURITY;

-- Fail-safe deny: unset app.tenant_id => NULLIF returns NULL => zero rows.
CREATE POLICY call_archives_isolation ON call_archives
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

CREATE POLICY recap_queue_isolation ON recap_queue
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

GRANT SELECT, INSERT, UPDATE ON call_archives, recap_queue TO mabel_app;
GRANT UPDATE ON service_area_zips TO mabel_app;
GRANT UPDATE (sms_sent, sms_reason) ON leads TO mabel_app;
-- No DELETE. Zip replace retires rows. Recap send is a later change.

COMMIT;
