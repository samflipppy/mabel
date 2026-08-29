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
    VOICE_AGENTS_URL,
    FakeXaiAgentsClient,
    FakeXaiVoiceAgentClient,
    VoiceAgentError,
    XaiHttpVoiceAgentClient,
    build_create_agent_body,
    parse_agent_id,
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
    fake = FakeXaiAgentsClient(next_id="agent_shop_a")
    assert FakeXaiAgentsClient is FakeXaiVoiceAgentClient
    agent_id = fake.create_from_template(shop_name="Example Plumbing")
    assert agent_id == "agent_shop_a"
    assert fake.created[0]["shop_name"] == "Example Plumbing"
    assert fake.created[0]["agent_id"] == "agent_shop_a"
    assert fake.created[0]["template_name"] == "Mabel"
    assert fake.created[0]["model"] == "grok-voice-think-fast-2.0"
    assert fake.created[0]["console_template"] is None
    body = fake.created[0]["body"]
    assert isinstance(body, dict)
    assert body["model"] == "grok-voice-think-fast-2.0"
    assert body["voice_clone"] is False
    tool_types = [tool.get("type") for tool in body["tools"]]
    assert "web_search" not in tool_types
    assert "x_search" not in tool_types


def test_create_body_is_our_template_with_mcp_url(monkeypatch) -> None:
    monkeypatch.setenv("MABEL_MCP_PUBLIC_URL", "https://mabel.fly.dev/mcp")
    body = build_create_agent_body(shop_name="Example Plumbing")
    assert body["name"] == "Mabel"
    assert body["model"] == "grok-voice-think-fast-2.0"
    assert "latest" not in body["model"]
    assert body["voice"] == "eve"
    assert body["voice_clone"] is False
    assert body["opening_disclosure"] == OPENING_DISCLOSURE
    assert "Never quote a price" in body["instructions"]
    assert "Never invent an arrival" in body["instructions"]
    assert body["tools"] == [
        {
            "type": "mcp",
            "server_label": "mabel",
            "server_url": "https://mabel.fly.dev/mcp",
            "allowed_tools": list(MABEL_MCP_TOOLS),
        }
    ]
    assert VOICE_AGENTS_URL == "https://api.x.ai/v1/voice-agents"


def test_parse_agent_id_reads_returned_id_and_never_invents() -> None:
    assert parse_agent_id({"id": "agent_real"}) == "agent_real"
    assert parse_agent_id({"agent_id": " agent_alt "}) == "agent_alt"
    assert parse_agent_id({"data": {"id": "agent_nested"}}) == "agent_nested"
    with pytest.raises(VoiceAgentError, match="did not get a voice agent id"):
        parse_agent_id({"ok": True})


def test_production_client_refuses_under_pytest(monkeypatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "not-a-real-key")
    assert os.environ.get("PYTEST_CURRENT_TEST")
    with pytest.raises(VoiceAgentError, match="will not call xAI from tests"):
        XaiHttpVoiceAgentClient().create_from_template(shop_name="Example Plumbing")


def test_production_client_fails_closed_without_key(monkeypatch) -> None:
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with pytest.raises(VoiceAgentError, match="xAI is not configured"):
        XaiHttpVoiceAgentClient().create_from_template(shop_name="Example Plumbing")


def test_production_client_uses_documented_shape_without_live_network(monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("XAI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("MABEL_MCP_PUBLIC_URL", "https://mabel.fly.dev/mcp")
    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"id": "agent_from_api"}

    def fake_post(url, *, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("httpx.post", fake_post)
    agent_id = XaiHttpVoiceAgentClient().create_from_template(shop_name="Example Plumbing")
    assert agent_id == "agent_from_api"
    assert captured["url"] == VOICE_AGENTS_URL
    assert captured["json"]["model"] == "grok-voice-think-fast-2.0"
    assert captured["json"]["tools"][0]["server_url"] == "https://mabel.fly.dev/mcp"
    assert "not-a-real-key" in captured["headers"]["Authorization"]


def test_production_client_fails_closed_without_inventing_an_id(monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("XAI_API_KEY", "not-a-real-key")

    class FakeResponse:
        status_code = 404

        def json(self):
            return {"error": "not found"}

    monkeypatch.setattr("httpx.post", lambda *args, **kwargs: FakeResponse())
    with pytest.raises(VoiceAgentError, match="could not create a voice agent"):
        XaiHttpVoiceAgentClient().create_from_template(shop_name="Example Plumbing")


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
