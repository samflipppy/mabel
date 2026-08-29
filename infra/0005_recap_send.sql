-- Draft only. Sam runs this. A bot does not.
-- Due-recap tenant list. Tenant is not known yet, so this is SECURITY DEFINER
-- the same way app.resolve_tenant_from_did is. Read-only. Application code
-- still never uses the migrator role. After this returns a tenant_id, the
-- send path SET LOCAL app.tenant_id and updates sent_at. No DELETE.

BEGIN;

CREATE FUNCTION app.due_recap_tenants(p_now timestamptz)
RETURNS TABLE (tenant_id uuid)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT DISTINCT q.tenant_id
    FROM recap_queue q
    WHERE q.sent_at IS NULL AND q.recap_at <= p_now
$$;

REVOKE ALL ON FUNCTION app.due_recap_tenants(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.due_recap_tenants(timestamptz) TO mabel_app;

COMMIT;
