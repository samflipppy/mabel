from __future__ import annotations

import re
from pathlib import Path

from mabel.voice.model import VOICE_MODEL

API_ROOT = Path(__file__).resolve().parents[2]


def test_voice_model_is_pinned() -> None:
    assert VOICE_MODEL == "grok-voice-think-fast-2.0"
    assert "latest" not in VOICE_MODEL


def test_repo_does_not_use_moving_voice_alias() -> None:
    hits = []
    for path in (API_ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"['\"]grok-voice-latest['\"]", text):
            hits.append(str(path))
    assert hits == []
