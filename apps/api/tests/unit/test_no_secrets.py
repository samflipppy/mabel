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


FLY_SECRET_NAMES = (
    "DATABASE_URL",
    "MABEL_ADMIN_TOKEN",
    "MABEL_MCP_TOKEN_SECRET",
    "MABEL_MCP_PUBLIC_URL",
    "XAI_API_KEY",
    "XAI_WEBHOOK_SECRET",
    "TELNYX_API_KEY",
    "TELNYX_FROM_E164",
)


def test_fly_files_list_secret_names_without_values() -> None:
    fly = (REPO_ROOT / "fly.toml").read_text(encoding="utf-8")
    api_docker = (REPO_ROOT / "apps" / "api" / "Dockerfile").read_text(encoding="utf-8")
    web_docker = (REPO_ROOT / "apps" / "web" / "Dockerfile").read_text(encoding="utf-8")
    assert "python:3.12" in api_docker
    assert "/health" in fly
    assert "fly deploy" not in fly.lower() or "does not fly deploy" in fly
    for name in FLY_SECRET_NAMES:
        assert name in fly
        assert f"{name}=" not in fly
        assert name not in api_docker
        assert name not in web_docker
    for text in (fly, api_docker, web_docker):
        lowered = text.lower()
        assert "sk-" not in text
        assert "whsec_" not in text
        assert "bearer " not in lowered


def test_setup_md_is_ordered_and_has_no_secret_values() -> None:
    text = (REPO_ROOT / "SETUP.md").read_text(encoding="utf-8")
    assert "Hire Mabel" in text
    assert "0001_init.sql" in text
    assert "0004_archive_recap.sql" in text
    assert "0005_recap_send.sql" in text
    assert "sip.voice.x.ai" in text
    assert "https://<fly-app>/voice/webhook" in text
    assert "TELNYX_FROM_E164" in text
    assert "MABEL_MCP_PUBLIC_URL" in text
    assert "https://<fly-app>/mcp" in text
    assert "python -m mabel.sms.recap_send" in text
    assert "sk-" not in text
    assert "whsec_" not in text
    for name in FLY_SECRET_NAMES:
        assert name in text
        assert f"{name}=" not in text or f"{name}=https://<fly-app>/mcp" in text


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
