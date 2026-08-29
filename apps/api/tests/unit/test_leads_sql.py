from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SQL_0001 = (REPO_ROOT / "infra" / "0001_init.sql").read_text(encoding="utf-8")


def test_leads_and_notes_have_rls_fail_safe() -> None:
    lead_check = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    assert "CREATE TABLE leads" in SQL_0001
    assert "CREATE TABLE notes" in SQL_0001
    assert f"WITH CHECK ({lead_check})" in SQL_0001
    assert "ENABLE ROW LEVEL SECURITY" in SQL_0001
    assert "FORCE ROW LEVEL SECURITY" in SQL_0001
    grant = "GRANT SELECT, INSERT, UPDATE ON tenants, inbound_dids, leads, notes TO mabel_app"
    assert grant in SQL_0001
    assert "GRANT DELETE" not in SQL_0001.upper().replace("  ", " ")


def test_dollars_won_is_numeric_never_float() -> None:
    assert "dollars_won numeric(12, 2)" in SQL_0001
    lowered = SQL_0001.lower()
    assert "double precision" not in lowered
    assert "float" not in lowered
    assert "Owner-entered. Never written from an LLM." in SQL_0001


def test_no_third_sql_file_for_leads() -> None:
    assert list((REPO_ROOT / "infra").glob("0003*.sql")) == []
