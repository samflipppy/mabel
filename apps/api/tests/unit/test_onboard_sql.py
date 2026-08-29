from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SQL_0001 = (REPO_ROOT / "infra" / "0001_init.sql").read_text(encoding="utf-8")
SQL_0002 = (REPO_ROOT / "infra" / "0002_shop_packet.sql").read_text(encoding="utf-8")
SQL_0003 = (REPO_ROOT / "infra" / "0003_xai_voice_agent.sql").read_text(encoding="utf-8")
SQL_0004 = (REPO_ROOT / "infra" / "0004_archive_recap.sql").read_text(encoding="utf-8")
SQL_0005 = (REPO_ROOT / "infra" / "0005_recap_send.sql").read_text(encoding="utf-8")


def test_rls_with_check_lets_insert_under_set_local() -> None:
    tenant_check = (
        "id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    )
    did_check = (
        "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    )
    zip_check = (
        "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    )
    assert f"WITH CHECK ({tenant_check})" in SQL_0001
    assert f"WITH CHECK ({did_check})" in SQL_0001
    assert f"WITH CHECK ({zip_check})" in SQL_0002
    assert "GRANT SELECT, INSERT, UPDATE ON tenants, inbound_dids" in SQL_0001
    assert "GRANT SELECT, INSERT, UPDATE ON service_area_zips TO mabel_app" in SQL_0002


def test_no_security_definer_write_function() -> None:
    # resolve_tenant_from_did is the only function. Insert uses SET LOCAL.
    assert SQL_0001.count("CREATE FUNCTION") == 1
    assert "resolve_tenant_from_did" in SQL_0001
    assert "CREATE FUNCTION" not in SQL_0002
    assert "SECURITY DEFINER" not in SQL_0002
    assert "BYPASSRLS" not in SQL_0002
    assert "CREATE FUNCTION" not in SQL_0003
    assert "SECURITY DEFINER" not in SQL_0003
    assert "BYPASSRLS" not in SQL_0003
    assert "CREATE FUNCTION" not in SQL_0004
    assert "SECURITY DEFINER" not in SQL_0004
    assert "BYPASSRLS" not in SQL_0004
    assert SQL_0005.count("CREATE FUNCTION") == 1
    assert "due_recap_tenants" in SQL_0005
    assert "SECURITY DEFINER" in SQL_0005
    assert "BYPASSRLS" not in SQL_0005
    assert "GRANT DELETE" not in SQL_0005.upper()
    assert "CREATE ROLE mabel_app LOGIN NOSUPERUSER NOBYPASSRLS" in SQL_0001
    assert "CREATE ROLE mabel_migrator LOGIN NOSUPERUSER BYPASSRLS" in SQL_0001


def test_draft_schema_has_onboard_columns_and_optional_agent_id() -> None:
    assert "status text NOT NULL DEFAULT 'draft'" in SQL_0001
    assert "owner_sms_e164" in SQL_0002
    assert "greeting_notes" in SQL_0002
    assert "CREATE TABLE service_area_zips" in SQL_0002
    assert list((REPO_ROOT / "infra").glob("0003*.sql")) == [
        REPO_ROOT / "infra" / "0003_xai_voice_agent.sql"
    ]
    assert list((REPO_ROOT / "infra").glob("0004*.sql")) == [
        REPO_ROOT / "infra" / "0004_archive_recap.sql"
    ]
    assert list((REPO_ROOT / "infra").glob("0005*.sql")) == [
        REPO_ROOT / "infra" / "0005_recap_send.sql"
    ]


def test_onboard_sql_has_no_float_money() -> None:
    for sql in (SQL_0001, SQL_0002, SQL_0003, SQL_0004, SQL_0005):
        lowered = sql.lower()
        assert "double precision" not in lowered
        assert "float" not in lowered
