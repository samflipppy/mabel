from __future__ import annotations

from pathlib import Path

from mabel.voice.webhook import AGENT_LIVE

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_agent_is_not_live() -> None:
    assert AGENT_LIVE is False


def test_onboard_does_not_take_agent_live() -> None:
    from mabel.shops import onboard, store
    from mabel.shops.http import router

    for module in (onboard, store):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "AGENT_LIVE = True" not in source
        assert "status" in source.lower() or module is store
    assert any(getattr(route, "path", "") == "/shops" for route in router.routes)
    assert AGENT_LIVE is False


def test_repo_does_not_commit_dotenv_or_key_files() -> None:
    hits: list[str] = []
    skip = {".git", "node_modules", ".next", ".venv"}
    for path in REPO_ROOT.rglob("*"):
        if any(part in skip for part in path.parts):
            continue
        if path.name in {".env", ".env.local", ".env.production", "credentials.json"}:
            hits.append(str(path.relative_to(REPO_ROOT)))
        if path.suffix in {".pem", ".key"}:
            hits.append(str(path.relative_to(REPO_ROOT)))
    assert hits == []


def test_ci_workflow_has_no_secrets_or_deploy() -> None:
    workflow = REPO_ROOT / ".github" / "workflows" / "test.yml"
    assert workflow.is_file()
    text = workflow.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "secrets" not in lowered
    assert "deploy" not in lowered
    assert "TELNYX" not in text
    assert "XAI_API_KEY" not in text
    assert "JOBBER" not in text
    assert "STRIPE" not in text
    assert "python-version: \"3.12\"" in text or "python-version: '3.12'" in text
    assert "packages/verticals" in text
    assert "apps/api[dev]" in text
    assert "pytest" in lowered
    assert "ruff" in lowered
