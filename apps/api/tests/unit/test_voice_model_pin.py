from __future__ import annotations

import re
from pathlib import Path

from mabel.voice.model import OPENING_DISCLOSURE, VOICE_MODEL

API_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[4]


def test_voice_model_is_pinned() -> None:
    assert VOICE_MODEL == "grok-voice-think-fast-2.0"
    assert "latest" not in VOICE_MODEL
    assert OPENING_DISCLOSURE == "This is an automated assistant and this call is recorded."


def test_repo_does_not_use_moving_voice_alias() -> None:
    hits = []
    for path in (API_ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"['\"]grok-voice-latest['\"]", text):
            hits.append(str(path))
    assert hits == []


def test_voice_docs_pin_and_do_not_join() -> None:
    text = (REPO_ROOT / "docs" / "xai-voice.md").read_text(encoding="utf-8")
    assert "grok-voice-think-fast-2.0" in text
    assert "We never use the alias" in text
    assert "$0.08" in text
    assert "$0.05" in text
    assert "web_search" in text
    assert "x_search" in text
    assert "$5" in text
    assert "sip.voice.x.ai" in text
    assert "byo_trunk" in text
    assert "audio/pcmu" in text
    assert "force_message" in text
    assert "interruptible" in text
    assert OPENING_DISCLOSURE in text
    assert "lookup_customer" in text
    assert "get_service_area" in text
    assert "check_availability" in text
    assert "create_lead" in text
    assert "escalate_emergency" in text
    assert "book_estimate" in text
    assert "get_job_history" in text
    assert "log_note" in text
    assert "This repo still does not open" in text
    assert "ONE xAI Voice Agent" in text
    assert "sk-" not in text
    assert "whsec_" not in text
