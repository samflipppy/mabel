from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SQL_0003 = (REPO_ROOT / "infra" / "0003_xai_voice_agent.sql").read_text(encoding="utf-8")


def test_voice_agent_sql_is_draft() -> None:
    assert "Draft only" in SQL_0003
    assert "Sam runs this" in SQL_0003
    assert "does not call xAI" in SQL_0003
    assert "does not take an agent live" in SQL_0003


def test_voice_agent_sql_adds_nullable_id_on_tenants() -> None:
    assert "ADD COLUMN xai_voice_agent_id text" in SQL_0003
    assert "ALTER TABLE tenants" in SQL_0003
    assert "NOT NULL" not in SQL_0003.replace("Draft only. Sam runs this. A bot does not.", "")
    assert "tenants_xai_voice_agent_id_not_blank" in SQL_0003
    assert "GRANT DELETE" not in SQL_0003.upper()
    assert "BYPASSRLS" not in SQL_0003
    assert "CREATE ROLE" not in SQL_0003
    assert "jsonb" not in SQL_0003.lower()


def test_voice_agent_sql_has_no_float_money() -> None:
    lowered = SQL_0003.lower()
    assert "double precision" not in lowered
    assert "float" not in lowered
    assert "numeric(12" not in lowered
    assert "dollars_won" not in lowered
    assert "$" not in SQL_0003
