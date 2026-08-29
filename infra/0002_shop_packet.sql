-- Draft only. Sam runs this. A bot does not.
-- Shop packet: structured facts for one tenant, not a PDF dump and not a JSON blob.
-- Columns on tenants plus service_area_zips keep RLS obvious. A blob would hide zip
-- isolation inside a document.
-- App role: no DELETE. Nothing irreversible without a human.

BEGIN;

ALTER TABLE tenants
    ADD COLUMN timezone text NOT NULL DEFAULT 'America/New_York',
    ADD COLUMN owner_sms_e164 text,
    ADD COLUMN after_hours_start time NOT NULL DEFAULT TIME '17:00',
    ADD COLUMN after_hours_end time NOT NULL DEFAULT TIME '08:00',
    ADD COLUMN greeting_notes text;

ALTER TABLE tenants
    ADD CONSTRAINT tenants_owner_sms_e164_format
        CHECK (
            owner_sms_e164 IS NULL
            OR owner_sms_e164 ~ '^\+[1-9][0-9]{7,14}$'
        ),
    ADD CONSTRAINT tenants_greeting_notes_no_money
        CHECK (
            greeting_notes IS NULL
            OR (
                greeting_notes !~ '\$'
                AND greeting_notes !~* 'dollar'
                AND greeting_notes !~ '[0-9]+\.[0-9]{2}'
            )
        );

CREATE TABLE service_area_zips (
    tenant_id uuid NOT NULL REFERENCES tenants (id),
    zip text NOT NULL,
    PRIMARY KEY (tenant_id, zip),
    CONSTRAINT service_area_zips_zip_format CHECK (zip ~ '^[0-9]{5}$')
);

ALTER TABLE service_area_zips ENABLE ROW LEVEL SECURITY;
ALTER TABLE service_area_zips FORCE ROW LEVEL SECURITY;

-- Fail-safe deny: unset app.tenant_id => NULLIF returns NULL => zero rows.
CREATE POLICY service_area_zips_isolation ON service_area_zips
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

GRANT SELECT, INSERT, UPDATE ON service_area_zips TO mabel_app;
-- No DELETE. Nothing irreversible without a human.

COMMIT;
