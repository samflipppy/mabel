-- Draft only. Sam runs this. A bot does not.
-- App role: no superuser, no BYPASSRLS.
-- Migrator role: BYPASSRLS, never used by application code.

BEGIN;

CREATE ROLE mabel_app LOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE mabel_migrator LOGIN NOSUPERUSER BYPASSRLS;

CREATE TABLE tenants (
    id uuid PRIMARY KEY,
    name text NOT NULL,
    vertical text NOT NULL,
    status text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'live', 'paused')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE inbound_dids (
    e164 text PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants (id)
);

CREATE TABLE leads (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants (id),
    name text NOT NULL,
    address text NOT NULL,
    callback text NOT NULL,
    problem text NOT NULL,
    urgency text NOT NULL,
    source text NOT NULL,
    emergency_code text,
    -- Owner-entered. Never written from an LLM.
    dollars_won numeric(12, 2),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE notes (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants (id),
    body text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
ALTER TABLE inbound_dids ENABLE ROW LEVEL SECURITY;
ALTER TABLE inbound_dids FORCE ROW LEVEL SECURITY;
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads FORCE ROW LEVEL SECURITY;
ALTER TABLE notes ENABLE ROW LEVEL SECURITY;
ALTER TABLE notes FORCE ROW LEVEL SECURITY;

-- Fail-safe deny: unset app.tenant_id => NULLIF returns NULL => zero rows.
CREATE POLICY tenants_isolation ON tenants
    USING (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

CREATE POLICY inbound_dids_isolation ON inbound_dids
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

CREATE POLICY leads_isolation ON leads
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

CREATE POLICY notes_isolation ON notes
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);

-- DID bootstrap. Tenant is not known yet, so this is SECURITY DEFINER.
-- Application code still never uses BYPASSRLS.
CREATE SCHEMA IF NOT EXISTS app;

CREATE FUNCTION app.resolve_tenant_from_did(p_did text)
RETURNS uuid
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT tenant_id FROM inbound_dids WHERE e164 = p_did
$$;

REVOKE ALL ON FUNCTION app.resolve_tenant_from_did(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.resolve_tenant_from_did(text) TO mabel_app;

GRANT SELECT, INSERT, UPDATE ON tenants, inbound_dids, leads, notes TO mabel_app;
-- No DELETE. Nothing irreversible without a human.

COMMIT;
