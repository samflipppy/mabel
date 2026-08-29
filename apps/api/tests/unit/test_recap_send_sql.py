from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SQL_0005 = (REPO_ROOT / "infra" / "0005_recap_send.sql").read_text(encoding="utf-8")


def test_recap_send_sql_is_draft() -> None:
    assert "Draft only" in SQL_0005
    assert "Sam runs this" in SQL_0005
    assert "due_recap_tenants" in SQL_0005
    assert "SECURITY DEFINER" in SQL_0005
    assert "BYPASSRLS" not in SQL_0005
    assert "GRANT DELETE" not in SQL_0005.upper()
    assert "CREATE ROLE" not in SQL_0005


def test_recap_send_sql_has_no_float_money() -> None:
    lowered = SQL_0005.lower()
    assert "double precision" not in lowered
    assert "float" not in lowered
    assert "numeric(12" not in lowered
    assert "dollars_won" not in lowered
    assert "$" not in SQL_0005.replace("$$", "")
