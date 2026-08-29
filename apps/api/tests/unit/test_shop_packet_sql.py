from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SQL = (REPO_ROOT / "infra" / "0002_shop_packet.sql").read_text(encoding="utf-8")


def test_shop_packet_sql_is_draft() -> None:
    assert "Draft only" in SQL
    assert "Sam runs this" in SQL


def test_shop_packet_sql_uses_rls_fail_safe() -> None:
    assert "ENABLE ROW LEVEL SECURITY" in SQL
    assert "FORCE ROW LEVEL SECURITY" in SQL
    assert "NULLIF(current_setting('app.tenant_id', true), '')::uuid" in SQL


def test_shop_packet_sql_has_no_delete_grant() -> None:
    assert "GRANT DELETE" not in SQL.upper().replace("  ", " ")
    assert "GRANT SELECT, INSERT, UPDATE ON service_area_zips TO mabel_app" in SQL
    assert "No DELETE" in SQL


def test_shop_packet_sql_is_columns_not_a_json_blob() -> None:
    assert "jsonb" not in SQL.lower()
    assert "CREATE TABLE service_area_zips" in SQL
    assert "ADD COLUMN timezone" in SQL
    assert "America/New_York" in SQL
    assert "owner_sms_e164" in SQL
    assert "greeting_notes" in SQL
    assert not any(
        line.strip().upper().startswith("ADD COLUMN") and "JSON" in line.upper()
        for line in SQL.splitlines()
    )


def test_shop_packet_sql_has_no_float_money() -> None:
    lowered = SQL.lower()
    assert "double precision" not in lowered
    assert "float" not in lowered
