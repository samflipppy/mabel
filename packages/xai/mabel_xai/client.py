"""The xAI client. One file, one place to be wrong.

Read `docs/xai_notes.md` before changing anything here. It is the ledger of
what we have verified against the API and what we are assuming. Every
assumption in this file carries an `# ASSUMPTION (A<n>)` comment pointing at
its row in that table. If you add a call, add the row.

Three rules this file exists to enforce:

**Fail closed without a key.** No `XAI_API_KEY` means the client refuses to
construct. It does not fall back to a mock, a recorded response, or a stub that
looks like it worked. See docs/BLOCKED.md #5.

**Refuse to run under pytest.** A test that reaches the real API is a test that
costs money, leaks a key into CI logs, and passes for reasons unrelated to the
code. `FakeXaiClient` is what tests bind.

**Never the `grok-voice-latest` alias.** It moved from Think Fast 1.0 to 2.0 on
2026-08-05 and took the per-minute price from $0.05 to $0.08 with it. We pin.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

logger = logging.getLogger(__name__)

# VERIFIED. Pinned, never the alias. See docs/xai_notes.md.
VOICE_MODEL = "grok-voice-think-fast-2.0"

# VERIFIED. The alias, named here only so a grep for it finds this comment.
FORBIDDEN_MODEL_ALIAS = "grok-voice-latest"

API_BASE = "https://api.x.ai/v1"
REALTIME_WS = "wss://api.x.ai/v1/realtime"
SIP_FQDN = "sip.voice.x.ai"

# VERIFIED. G.711 mu-law at 8kHz, in and out. No transcoding.
AUDIO_FORMAT = "audio/pcmu"
AUDIO_RATE = 8000

# VERIFIED. The exhaustive tool list. Not web_search, not x_search, not
# file_search. See the conflict note in docs/xai_notes.md for why this is nine
# and not eight.
ALLOWED_TOOLS: tuple[str, ...] = (
    "lookup_customer",
    "get_service_area",
    "check_availability",
    "create_lead",
    "escalate_emergency",
    "book_estimate",
    "get_job_history",
    "answer_question",
    "log_note",
)

# VERIFIED. Team-wide, and the reason we alert at 7.
MAX_CONCURRENT_SESSIONS = 10
CONCURRENCY_ALERT_THRESHOLD = 7

# VERIFIED. Not a practical concern for a call about a burst pipe.
MAX_SESSION_MINUTES = 120

DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class XaiUnavailable(RuntimeError):
    """No API key. We refuse rather than degrade. See docs/BLOCKED.md #5."""


class XaiRefusedUnderTest(RuntimeError):
    """Something tried to reach the live API from a test."""


class XaiError(RuntimeError):
    """The API answered, and the answer was not usable."""


def _under_pytest() -> bool:
    return "pytest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST"))


def api_key() -> str:
    key = os.environ.get("XAI_API_KEY")
    if not key:
        raise XaiUnavailable(
            "XAI_API_KEY is unset. Mabel does not invent a credential and does not "
            "run against a stub that pretends to be xAI. See docs/BLOCKED.md #5."
        )
    return key


def join_url(call_id: str) -> str:
    """The realtime socket for an inbound SIP call.

    VERIFIED: authenticated with the API key. Ephemeral client secrets are not
    supported for SIP `call_id` sessions.

    VERIFIED: `model` is ignored on a `call_id` session — the session binds to
    the inbound call. We send it anyway, because it is correct for direct
    sessions and because a silent behaviour change is easier to spot when we
    are explicit.
    """
    return f"{REALTIME_WS}?call_id={call_id}&model={VOICE_MODEL}"


def sip_uri(number_e164: str) -> str:
    """VERIFIED. `sip:{number}@sip.voice.x.ai;transport=tls`, origin byo_trunk."""
    return f"sip:{number_e164}@{SIP_FQDN};transport=tls"


@dataclass(frozen=True, slots=True)
class VoiceAgentTemplate:
    """Our template, not one of xAI's console templates.

    Per AGENTS.md: each client gets their own Voice Agent so call logs,
    collections and MCP connections are per shop. We never click 'Customer
    Support' — the agent is created from this.
    """

    business_name: str
    instructions: str
    mcp_url: str
    voice: str = "carina"
    model: str = VOICE_MODEL
    allowed_tools: tuple[str, ...] = ALLOWED_TOOLS

    def payload(self) -> dict[str, Any]:
        return {
            "name": f"Mabel for {self.business_name}",
            "model": self.model,
            "instructions": self.instructions,
            "voice": self.voice,
            # No voice clone. Ever.
            "tools": [
                {
                    "type": "mcp",
                    "server_label": "mabel",
                    "server_url": self.mcp_url,
                    "allowed_tools": list(self.allowed_tools),
                }
            ],
        }


class XaiClient:
    """The live client. Constructing one without a key raises; constructing one
    under pytest raises."""

    def __init__(
        self, *, key: str | None = None, transport: httpx.AsyncBaseTransport | None = None
    ):
        if _under_pytest() and transport is None:
            raise XaiRefusedUnderTest(
                "XaiClient refuses to run under pytest. Bind FakeXaiClient instead. "
                "A test that reaches the live API costs money and passes for the "
                "wrong reasons."
            )
        self._key = key if key is not None else api_key()
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=DEFAULT_TIMEOUT,
            transport=transport,
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> XaiClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def create_voice_agent(self, template: VoiceAgentTemplate) -> str | None:
        """Create this shop's agent. Returns the agent id, or None.

        # ASSUMPTION (A1): `POST /v1/voice-agents` is not in the public docs as
        # of 2026-08-29. This is the most likely shape by convention with the
        # rest of /v1. Returning None rather than raising is deliberate:
        # onboarding leaves `tenants.xai_agent_id` NULL, the shop still drafts,
        # and the agent can be minted by hand in console.x.ai until the route
        # is confirmed. A missing agent id must not block a sale.
        """
        try:
            response = await self._client.post("/voice-agents", json=template.payload())
        except httpx.HTTPError as exc:
            logger.warning("voice agent create failed at the transport: %s", exc)
            return None

        if response.status_code >= 400:
            logger.warning(
                "voice agent create returned %s. If this is 404, assumption A1 in "
                "docs/xai_notes.md is wrong and the route needs confirming.",
                response.status_code,
            )
            return None

        body = response.json()
        agent_id = body.get("id") or body.get("agent_id")
        if not agent_id:
            logger.warning("voice agent create returned no id: keys were %s", sorted(body))
            return None
        return str(agent_id)

    async def fetch_transcript(self, call_id: str) -> dict[str, Any] | None:
        """# ASSUMPTION (A8): the retrieval route is not documented.

        Invariant 7 does not depend on this working. `postcall` reconstructs
        the transcript from the turns the media process observed live, and
        treats this as an enrichment. We are never dependent on xAI's storage
        at query time — their resumption cache drops history after ~30 minutes
        idle and is not a store.
        """
        return await self._maybe_get(f"/realtime/calls/{call_id}/transcript")

    async def fetch_recording(self, call_id: str) -> bytes | None:
        """# ASSUMPTION (A8). Same caveat as fetch_transcript."""
        try:
            response = await self._client.get(f"/realtime/calls/{call_id}/recording")
        except httpx.HTTPError as exc:
            logger.warning("recording fetch failed for %s: %s", call_id, exc)
            return None
        if response.status_code >= 400:
            logger.warning("recording fetch returned %s for %s", response.status_code, call_id)
            return None
        return response.content

    async def _maybe_get(self, path: str) -> dict[str, Any] | None:
        try:
            response = await self._client.get(path)
        except httpx.HTTPError as exc:
            logger.warning("GET %s failed: %s", path, exc)
            return None
        if response.status_code >= 400:
            logger.warning("GET %s returned %s", path, response.status_code)
            return None
        try:
            return response.json()
        except ValueError:
            logger.warning("GET %s returned a body that is not JSON", path)
            return None


@dataclass
class FakeXaiClient:
    """What tests bind. Records what was asked of it and answers from a script.

    It exists so a test can assert on the *shape* we send — that the model is
    pinned, that `web_search` is absent, that the tool list is the nine — with
    no socket anywhere near it.
    """

    agent_id: str | None = "agent_fake"
    transcript: dict[str, Any] | None = None
    recording: bytes | None = None
    calls: list[tuple[str, Any]] = field(default_factory=list)

    async def aclose(self) -> None:
        return None

    async def __aenter__(self) -> FakeXaiClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def create_voice_agent(self, template: VoiceAgentTemplate) -> str | None:
        self.calls.append(("create_voice_agent", template.payload()))
        return self.agent_id

    async def fetch_transcript(self, call_id: str) -> dict[str, Any] | None:
        self.calls.append(("fetch_transcript", call_id))
        return self.transcript

    async def fetch_recording(self, call_id: str) -> bytes | None:
        self.calls.append(("fetch_recording", call_id))
        return self.recording


Client = XaiClient | FakeXaiClient


def concurrency_state(active_sessions: int) -> Literal["ok", "alert", "at_limit"]:
    """10 concurrent sessions per team is the documented ceiling. Ask xAI for a
    raise well before it bites, because at the limit the eleventh caller does
    not reach Mabel at all."""
    if active_sessions >= MAX_CONCURRENT_SESSIONS:
        return "at_limit"
    if active_sessions >= CONCURRENCY_ALERT_THRESHOLD:
        return "alert"
    return "ok"
