"""Per-shop xAI Voice Agent. One agent per client, from OUR template.

We do not click console templates like Customer Support. She is Mabel.
This stub does not call xAI. Tests never need a key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

from mabel.shops.packet import reject_dollar_text
from mabel.voice.model import OPENING_DISCLOSURE, VOICE_MODEL

# Eight tools only. Kept here so the template does not import MCP.
MABEL_MCP_TOOLS = (
    "lookup_customer",
    "get_service_area",
    "check_availability",
    "create_lead",
    "escalate_emergency",
    "book_estimate",
    "get_job_history",
    "log_note",
)

FORBIDDEN_CONSOLE_TEMPLATES = frozenset({"Customer Support"})

MABEL_AGENT_INSTRUCTIONS = (
    "You are Mabel. You answer the phone when a contractor can't. "
    "Open with the disclosure. Never quote a price. Never invent an arrival time. "
    "Never use web_search or x_search."
)


class VoiceAgentError(RuntimeError):
    pass


@dataclass(frozen=True)
class MabelVoiceAgentTemplate:
    """What onboard would send to console.x.ai. Not a Customer Support template."""

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
        _assert_our_template(MABEL_VOICE_AGENT_TEMPLATE)
        agent_id = self.next_id
        self.created.append(
            {
                "shop_name": shop_name,
                "agent_id": agent_id,
                "template_name": MABEL_VOICE_AGENT_TEMPLATE.name,
                "model": MABEL_VOICE_AGENT_TEMPLATE.model,
                "console_template": MABEL_VOICE_AGENT_TEMPLATE.console_template,
            }
        )
        return agent_id


class StubXaiVoiceAgentClient:
    """Would create a per-shop agent. This PR does not call xAI."""

    def create_from_template(self, *, shop_name: str) -> str:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            raise VoiceAgentError("Mabel will not call xAI from tests.")
        if not _xai_key():
            raise VoiceAgentError("Mabel cannot create a voice agent. xAI is not configured.")
        _assert_our_template(MABEL_VOICE_AGENT_TEMPLATE)
        raise VoiceAgentError("Mabel is not creating voice agents from this stub.")


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
