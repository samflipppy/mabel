from __future__ import annotations

from pathlib import Path

from mabel.voice.webhook import AGENT_LIVE

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_agent_is_not_live() -> None:
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
