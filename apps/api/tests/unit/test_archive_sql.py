from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SQL = (REPO_ROOT / "infra" / "0004_archive_recap.sql").read_text(encoding="utf-8")


def test_archive_recap_sql_is_draft() -> None:
    assert "Draft only" in SQL
    assert "Sam runs this" in SQL
    assert "CREATE TABLE call_archives" in SQL
    assert "CREATE TABLE recap_queue" in SQL
    assert "sms_sent" in SQL
    assert "retired_at" in SQL
    assert "sent_at" in SQL


def test_archive_recap_sql_uses_rls_fail_safe() -> None:
    assert "ENABLE ROW LEVEL SECURITY" in SQL
    assert "FORCE ROW LEVEL SECURITY" in SQL
    assert "NULLIF(current_setting('app.tenant_id', true), '')::uuid" in SQL
    assert "GRANT SELECT, INSERT, UPDATE ON call_archives, recap_queue TO mabel_app" in SQL
    assert "GRANT DELETE" not in SQL.upper()
    assert "No DELETE" in SQL
    assert "BYPASSRLS" not in SQL
    assert "jsonb" not in SQL.lower()


def test_archive_recap_sql_has_no_float_money() -> None:
    lowered = SQL.lower()
    assert "double precision" not in lowered
    assert "float" not in lowered
    assert "numeric(12" not in lowered
    assert "dollars_won" not in lowered
