"""Per-shop xAI Voice Agent. One agent per client, from OUR template.

We do not click console templates like Customer Support. She is Mabel.

Public docs.x.ai (researched 2026-08-29) document speech-to-speech join,
session.update, custom voices, and the Management API for keys. They do not
document a Voice Agents create route. This client POSTs the most likely
REST shape, `https://api.x.ai/v1/voice-agents`, and fails closed if the
call does not return an id. Until that API is confirmed, console.x.ai
login can still mint the agent; store the id on the tenant.

Tests bind FakeXaiAgentsClient. They never need a key and they never POST
to xAI. The production client reads XAI_API_KEY at create time and refuses
to run under pytest. Never invent a key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from mabel.platform.config import mcp_public_url, xai_ready
from mabel.shops.packet import reject_dollar_text
from mabel.voice.model import OPENING_DISCLOSURE, VOICE_MODEL

# Eight tools only. Kept here so the template does not import MCP.
MABEL_MCP_TOOLS = (
    "lookup_customer",
    "get_service_area",
    "check_availability",
    "get_job_history",
    "create_lead",
    "escalate_emergency",
    "book_estimate",
    "log_note",
)

FORBIDDEN_CONSOLE_TEMPLATES = frozenset({"Customer Support"})

MABEL_AGENT_INSTRUCTIONS = (
    "You are Mabel. You answer the phone when a contractor can't. "
    "Open with the disclosure. Never quote a price. Never invent an arrival time. "
    "Never use web_search or x_search."
)

# Most likely REST shape. Not confirmed on docs.x.ai as of 2026-08-29.
VOICE_AGENTS_URL = "https://api.x.ai/v1/voice-agents"
SESSION_VOICE = "eve"


class VoiceAgentError(RuntimeError):
    pass


@dataclass(frozen=True)
class MabelVoiceAgentTemplate:
    """What we send when creating a per-shop agent. Not a Customer Support template."""

    name: str = "Mabel"
    model: str = VOICE_MODEL
    opening_disclosure: str = OPENING_DISCLOSURE
    instructions: str = MABEL_AGENT_INSTRUCTIONS
    voice_clone: bool = False
    web_search: bool = False
    x_search: bool = False
    tools: tuple[str, ...] = MABEL_MCP_TOOLS
    never_quote_price: bool = True
    never_invent_arrival: bool = True
    console_template: str | None = None


MABEL_VOICE_AGENT_TEMPLATE = MabelVoiceAgentTemplate()


def reject_collection_upload(text: str, *, title: str | None = None) -> None:
    """Collections may hold non-price shop docs. Dollar-looking text is out."""
    if title is not None and title.strip():
        reject_dollar_text(title, field="collection docs")
    reject_dollar_text(text, field="collection docs")


class XaiVoiceAgentClient(Protocol):
    def create_from_template(self, *, shop_name: str) -> str:
        """Create this shop's agent from OUR template. Returns the agent id."""


@dataclass
class FakeXaiVoiceAgentClient:
    """In-memory stand-in. The only client pytest should use."""

    created: list[dict[str, object]] = field(default_factory=list)
    next_id: str = "agent_test_1"

    def create_from_template(self, *, shop_name: str) -> str:
        body = build_create_agent_body(shop_name=shop_name)
        _assert_our_template(MABEL_VOICE_AGENT_TEMPLATE)
        agent_id = self.next_id
        self.created.append(
            {
                "shop_name": shop_name,
                "agent_id": agent_id,
                "template_name": MABEL_VOICE_AGENT_TEMPLATE.name,
                "model": MABEL_VOICE_AGENT_TEMPLATE.model,
                "console_template": MABEL_VOICE_AGENT_TEMPLATE.console_template,
                "body": body,
            }
        )
        return agent_id


# Name Sam asked for. Same object as FakeXaiVoiceAgentClient.
FakeXaiAgentsClient = FakeXaiVoiceAgentClient


class XaiHttpVoiceAgentClient:
    """POST /v1/voice-agents. Key stays in the environment. Never written to a file."""

    def create_from_template(self, *, shop_name: str) -> str:
        # Tests must use FakeXaiAgentsClient. No live xAI calls from pytest.
        if os.environ.get("PYTEST_CURRENT_TEST"):
            raise VoiceAgentError("Mabel will not call xAI from tests.")
        if not xai_ready():
            raise VoiceAgentError("Mabel cannot create a voice agent. xAI is not configured.")
        body = build_create_agent_body(shop_name=shop_name)
        _assert_our_template(MABEL_VOICE_AGENT_TEMPLATE)
        key = _xai_key()
        if not key:
            raise VoiceAgentError("Mabel cannot create a voice agent. xAI is not configured.")
        import httpx

        # Do not log headers. The bearer token is the key.
        try:
            response = httpx.post(
                VOICE_AGENTS_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            raise VoiceAgentError("Mabel could not create a voice agent.") from exc
        if response.status_code >= 400:
            raise VoiceAgentError("Mabel could not create a voice agent.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise VoiceAgentError("Mabel could not create a voice agent.") from exc
        if not isinstance(payload, dict):
            raise VoiceAgentError("Mabel could not create a voice agent.")
        return parse_agent_id(payload)


# Older name. Same production client.
StubXaiVoiceAgentClient = XaiHttpVoiceAgentClient


def build_create_agent_body(*, shop_name: str) -> dict[str, Any]:
    """Documented REST body. Pin, disclosure, eight MCP tools, no search, no clone."""
    _assert_our_template(MABEL_VOICE_AGENT_TEMPLATE)
    reject_dollar_text(shop_name, field="shop name")
    instructions = (
        f"{MABEL_VOICE_AGENT_TEMPLATE.instructions} "
        f"Shop: {shop_name}. "
        f"Opening disclosure: {MABEL_VOICE_AGENT_TEMPLATE.opening_disclosure}"
    )
    reject_dollar_text(instructions, field="voice agent instructions")
    return {
        "name": MABEL_VOICE_AGENT_TEMPLATE.name,
        "model": MABEL_VOICE_AGENT_TEMPLATE.model,
        "voice": SESSION_VOICE,
        "instructions": instructions,
        "opening_disclosure": MABEL_VOICE_AGENT_TEMPLATE.opening_disclosure,
        "voice_clone": False,
        "tools": [
            {
                "type": "mcp",
                "server_label": "mabel",
                "server_url": mcp_public_url(),
                "allowed_tools": list(MABEL_MCP_TOOLS),
            }
        ],
    }


def parse_agent_id(payload: dict[str, Any]) -> str:
    """Read an id the API returned. Never invent one."""
    for key in ("id", "agent_id", "voice_agent_id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for nested_key in ("voice_agent", "agent", "data"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            return parse_agent_id(nested)
    raise VoiceAgentError("Mabel did not get a voice agent id.")


_bound_client: XaiVoiceAgentClient | None = None


def bind_voice_agent_client(client: XaiVoiceAgentClient | None) -> XaiVoiceAgentClient | None:
    """Tests inject FakeXaiAgentsClient here. Production does not."""
    global _bound_client
    previous = _bound_client
    _bound_client = client
    return previous


def voice_agent_client() -> XaiVoiceAgentClient:
    if _bound_client is not None:
        return _bound_client
    return XaiHttpVoiceAgentClient()


def maybe_create_voice_agent(*, shop_name: str, provided_id: str | None = None) -> str | None:
    """Create from our template when the key is set. Never block onboard.

    Caller-supplied id wins (tests, or an id minted in console). Missing key
    or a failed create leaves null. Shop still drafts.
    """
    if provided_id is not None and str(provided_id).strip():
        return str(provided_id).strip()
    if not xai_ready():
        return None
    try:
        agent_id = voice_agent_client().create_from_template(shop_name=shop_name)
    except VoiceAgentError:
        return None
    if agent_id is None or not str(agent_id).strip():
        return None
    return str(agent_id).strip()


def _assert_our_template(template: MabelVoiceAgentTemplate) -> None:
    if template.console_template in FORBIDDEN_CONSOLE_TEMPLATES:
        raise VoiceAgentError("Mabel is not the Customer Support template.")
    if template.model != VOICE_MODEL or "latest" in template.model:
        raise VoiceAgentError("Mabel's voice model is pinned.")
    if template.voice_clone or template.web_search or template.x_search:
        raise VoiceAgentError("Mabel does not clone a voice or search the web.")
    if template.tools != MABEL_MCP_TOOLS:
        raise VoiceAgentError("Mabel only gets her eight tools.")
    if not template.never_quote_price or not template.never_invent_arrival:
        raise VoiceAgentError("Mabel will not quote a price or invent an arrival.")


def _xai_key() -> str | None:
    value = os.environ.get("XAI_API_KEY")
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
