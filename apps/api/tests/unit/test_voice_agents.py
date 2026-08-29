from __future__ import annotations

import os
from pathlib import Path

import pytest

from mabel.mcp.tools import TOOL_NAMES
from mabel.shops.packet import PacketError
from mabel.voice.agents import (
    FORBIDDEN_CONSOLE_TEMPLATES,
    MABEL_AGENT_INSTRUCTIONS,
    MABEL_MCP_TOOLS,
    MABEL_VOICE_AGENT_TEMPLATE,
    FakeXaiVoiceAgentClient,
    StubXaiVoiceAgentClient,
    VoiceAgentError,
    reject_collection_upload,
)
from mabel.voice.model import OPENING_DISCLOSURE, VOICE_MODEL
from mabel.voice.webhook import AGENT_LIVE

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_template_is_mabel_not_customer_support() -> None:
    template = MABEL_VOICE_AGENT_TEMPLATE
    assert template.name == "Mabel"
    assert template.model == VOICE_MODEL
    assert template.model == "grok-voice-think-fast-2.0"
    assert "latest" not in template.model
    assert template.opening_disclosure == OPENING_DISCLOSURE
    assert template.voice_clone is False
    assert template.web_search is False
    assert template.x_search is False
    assert template.tools == TOOL_NAMES
    assert template.tools == MABEL_MCP_TOOLS
    assert len(template.tools) == 8
    assert template.never_quote_price is True
    assert template.never_invent_arrival is True
    assert template.console_template is None
    assert "Customer Support" in FORBIDDEN_CONSOLE_TEMPLATES
    assert template.console_template not in FORBIDDEN_CONSOLE_TEMPLATES
    assert "Never quote a price" in MABEL_AGENT_INSTRUCTIONS
    assert "Never invent an arrival" in MABEL_AGENT_INSTRUCTIONS
    assert AGENT_LIVE is False


def test_fake_client_creates_from_our_template_not_xai() -> None:
    fake = FakeXaiVoiceAgentClient(next_id="agent_shop_a")
    agent_id = fake.create_from_template(shop_name="Example Plumbing")
    assert agent_id == "agent_shop_a"
    assert fake.created == [
        {
            "shop_name": "Example Plumbing",
            "agent_id": "agent_shop_a",
            "template_name": "Mabel",
            "model": "grok-voice-think-fast-2.0",
            "console_template": None,
        }
    ]


def test_stub_client_refuses_under_pytest(monkeypatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "not-a-real-key")
    assert os.environ.get("PYTEST_CURRENT_TEST")
    with pytest.raises(VoiceAgentError, match="will not call xAI from tests"):
        StubXaiVoiceAgentClient().create_from_template(shop_name="Example Plumbing")


def test_stub_client_fails_closed_without_key(monkeypatch) -> None:
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with pytest.raises(VoiceAgentError, match="xAI is not configured"):
        StubXaiVoiceAgentClient().create_from_template(shop_name="Example Plumbing")


def test_stub_client_does_not_call_xai_when_key_present(monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("XAI_API_KEY", "not-a-real-key")
    with pytest.raises(VoiceAgentError, match="not creating voice agents from this stub"):
        StubXaiVoiceAgentClient().create_from_template(shop_name="Example Plumbing")


@pytest.mark.parametrize(
    "text",
    [
        "After-hours rate is $99",
        "Trip fee is 89.00",
        "We charge ninety dollars",
        "Say USD 50 for the visit",
        "Callout is 1,200.00",
    ],
)
def test_dollar_looking_collection_uploads_are_rejected(text: str) -> None:
    with pytest.raises(PacketError, match="dollar"):
        reject_collection_upload(text)
    with pytest.raises(PacketError, match="dollar"):
        reject_collection_upload("Hours and zips only.", title=text)


def test_non_price_collection_docs_are_kept() -> None:
    reject_collection_upload(
        "We cover Lakewood and Cleveland. Ask how the dog is.",
        title="Service area notes",
    )


def test_agents_md_and_docs_are_per_shop() -> None:
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    docs = (REPO_ROOT / "docs" / "xai-voice.md").read_text(encoding="utf-8")
    assert "ONE xAI Voice Agent" not in agents
    assert "ONE xAI Voice Agent" not in docs
    assert "own xAI Voice Agent" in agents
    assert "own xAI Voice Agent" in docs
    assert "Customer Support" in agents
    assert "Customer Support" in docs
    assert "auto merge every time" in agents
    assert "do not ask Sam to view or confirm" in agents
    assert "collection docs" in docs.lower() or "non-price shop docs" in docs
    assert "sk-" not in docs
    assert "whsec_" not in docs
