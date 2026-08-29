"""`agent_configs` row -> the `session.update` payload.

The exact shape is 03-VOICE.md's, and several of its keys are assumptions
rather than verified API — A4 through A7 in `docs/xai_notes.md`. They are
grouped here rather than scattered so that when the first real session either
accepts or rejects this payload, there is one file to correct.

What this file must get right regardless of those assumptions:

- The model is pinned. Never the alias.
- `allowed_tools` is exactly the nine. No `web_search`, no `x_search`, no
  `file_search`.
- The MCP `authorization` is the short-lived token minted from the dialed
  number, not a long-lived key.
- Audio is G.711 mu-law both directions, so nothing transcodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from mabel_mcp.tokens import bearer, mint_call_token
from mabel_xai.client import ALLOWED_TOOLS, AUDIO_FORMAT, AUDIO_RATE, VOICE_MODEL

# 03-VOICE.md's turn detection. ASSUMPTION A5: the parameter names match the
# realtime convention xAI otherwise tracks; we have not seen them accepted.
TURN_DETECTION: dict[str, Any] = {
    "type": "server_vad",
    # High, because a homeowner with water running in the background will
    # otherwise interrupt her constantly.
    "threshold": 0.85,
    "silence_duration_ms": 700,
    "prefix_padding_ms": 333,
    "idle_timeout_ms": 8000,
}

TRANSCRIPTION_MODEL = "grok-transcribe"


@dataclass(frozen=True, slots=True)
class SessionInputs:
    tenant_id: UUID
    call_id: str
    instructions: str
    voice: str
    speaking_rate: float
    keyterms: list[str]
    mcp_url: str


def build_session_update(inputs: SessionInputs, *, token_key: str | None = None) -> dict[str, Any]:
    """The `session.update` message.

    The MCP token is minted here, at the moment the session opens, because its
    fifteen minutes should start now rather than whenever the config was
    fetched.
    """
    token = mint_call_token(inputs.tenant_id, inputs.call_id, key=token_key)

    return {
        "type": "session.update",
        "session": {
            "instructions": inputs.instructions,
            "voice": inputs.voice,
            "audio": {
                "input": {
                    "format": {"type": AUDIO_FORMAT, "rate": AUDIO_RATE},
                    "transcription": {
                        "model": TRANSCRIPTION_MODEL,
                        # ASSUMPTION A4: keyterms is named in 03-VOICE.md but
                        # not in the public reference. Street names and local
                        # brands are exactly what an 8kHz phone line mangles,
                        # so it is worth sending.
                        "keyterms": list(inputs.keyterms),
                    },
                },
                "output": {
                    "format": {"type": AUDIO_FORMAT, "rate": AUDIO_RATE},
                    # ASSUMPTION A6: `speed` as the speaking-rate knob.
                    "speed": inputs.speaking_rate,
                },
            },
            "turn_detection": dict(TURN_DETECTION),
            # ASSUMPTION A7: the MCP tool entry shape.
            "tools": [
                {
                    "type": "mcp",
                    "server_url": inputs.mcp_url,
                    "server_label": "mabel",
                    "authorization": bearer(token),
                    "allowed_tools": list(ALLOWED_TOOLS),
                }
            ],
        },
    }


def build_opening_disclosure(text: str) -> dict[str, Any]:
    """The opening line, as a `force_message`.

    VERIFIED shape (docs/xai_notes.md): `conversation.item.create` with
    `item.type` = `force_message` and `interruptible` = false. Do **not** send
    `response.create` after it — the force message is the turn, and following
    it with one makes her say the disclosure and then immediately talk over
    herself.

    It is a force message rather than a prompt instruction because a disclosure
    the model paraphrases is a disclosure that may no longer disclose anything.
    """
    return {
        "type": "conversation.item.create",
        "item": {
            "type": "force_message",
            "role": "assistant",
            "interruptible": False,
            "content": [{"type": "text", "text": text}],
        },
    }


def assert_session_is_safe(payload: dict[str, Any]) -> None:
    """Last check before the payload goes over the socket.

    Everything here is something that would be expensive, wrong, or both, and
    that a careless edit upstream could introduce without any test noticing.
    """
    session = payload.get("session", {})

    tools = session.get("tools", [])
    if len(tools) != 1 or tools[0].get("type") != "mcp":
        raise ValueError("expected exactly one MCP tool entry")

    allowed = tools[0].get("allowed_tools", [])
    if list(allowed) != list(ALLOWED_TOOLS):
        raise ValueError(f"allowed_tools drifted from the nine: {allowed}")

    for banned in ("web_search", "x_search", "file_search"):
        if banned in str(payload):
            # $5/1k calls, and she starts answering from the open internet.
            raise ValueError(f"{banned} must never be enabled")

    authorization = tools[0].get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise ValueError("the MCP entry must carry a bearer token")
    if "xai-" in authorization:
        # The MCP server takes our short-lived call token. Sending the xAI API
        # key here would hand every tool call the keys to the account.
        raise ValueError("that looks like an API key, not a call token")

    audio = session.get("audio", {})
    for direction in ("input", "output"):
        fmt = audio.get(direction, {}).get("format", {})
        if fmt.get("type") != AUDIO_FORMAT:
            raise ValueError(f"{direction} audio must be {AUDIO_FORMAT}, got {fmt.get('type')}")


def pinned_model() -> str:
    """Exposed so the media process can pin it on a direct (non-SIP) session.

    On a SIP `call_id` session the `model` parameter is ignored — the session
    binds to the inbound call. We send it anyway, because a silent behaviour
    change is easier to notice when we were explicit.
    """
    return VOICE_MODEL
