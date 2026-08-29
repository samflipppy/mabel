"""Inbound call session. Fail closed. Tests never open a WebSocket.

After webhook signature + DID tenant resolve + keys present: mint a tenant
MCP token, send session.update from our template plus the shop packet (no
dollar figures), then force_message disclosure with interruptible false.
Do not send response.create after that opening line.

AGENT_LIVE stays false. joined is true only for an in-memory FakeSessionTransport.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from mabel.platform.config import mcp_public_url
from mabel.shops.packet import PacketError, ShopPacket, packet_for, reject_dollar_text
from mabel.voice.agents import MABEL_MCP_TOOLS, MABEL_VOICE_AGENT_TEMPLATE
from mabel.voice.archive import CallArchive, archive_call
from mabel.voice.model import OPENING_DISCLOSURE, VOICE_MODEL

REALTIME_URL = "wss://api.x.ai/v1/realtime"
DEFAULT_MCP_URL = "http://127.0.0.1:8000/mcp"
SESSION_VOICE = "eve"


class SessionError(RuntimeError):
    """This call cannot join."""


class SessionTransport(Protocol):
    in_memory: bool

    async def join(self, *, call_id: str, api_key: str) -> None: ...

    async def send(self, payload: dict[str, Any]) -> None: ...

    async def close(self) -> None: ...


@dataclass
class FakeSessionTransport:
    """In-memory stand-in. The only transport pytest should use."""

    in_memory: bool = True
    sent: list[dict[str, Any]] = field(default_factory=list)
    joined: bool = False
    closed: bool = False
    call_id: str | None = None

    async def join(self, *, call_id: str, api_key: str) -> None:
        del api_key
        self.call_id = call_id
        self.joined = True

    async def send(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self) -> None:
        self.closed = True

    @property
    def session_updates(self) -> list[dict[str, Any]]:
        return [item for item in self.sent if item.get("type") == "session.update"]

    @property
    def force_messages(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for item in self.sent:
            if item.get("type") != "conversation.item.create":
                continue
            if (item.get("item") or {}).get("type") == "force_message":
                found.append(item)
        return found

    @property
    def response_creates(self) -> list[dict[str, Any]]:
        return [item for item in self.sent if item.get("type") == "response.create"]


class WebsocketSessionTransport:
    """Production client. Refuses under pytest. Never logs the key."""

    in_memory: bool = False

    def __init__(self) -> None:
        self._ws: Any = None
        self._cm: Any = None
        self.joined: bool = False
        self.closed: bool = False

    async def join(self, *, call_id: str, api_key: str) -> None:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            raise SessionError("Mabel will not open a WebSocket from tests.")
        if not api_key or not str(api_key).strip():
            raise SessionError("Mabel cannot join this call. xAI is not configured.")
        import websockets

        url = f"{REALTIME_URL}?call_id={call_id}&model={VOICE_MODEL}"
        self._cm = websockets.connect(
            url,
            additional_headers={"Authorization": f"Bearer {api_key}"},
        )
        self._ws = await self._cm.__aenter__()
        self.joined = True

    async def send(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            raise SessionError("Mabel is not in a call.")
        await self._ws.send(json.dumps(payload))

    async def close(self) -> None:
        self.closed = True
        if self._cm is None:
            return
        await self._cm.__aexit__(None, None, None)
        self._cm = None
        self._ws = None


@dataclass(frozen=True)
class HeldSession:
    call_id: str
    tenant_id: UUID
    transport: SessionTransport


@dataclass(frozen=True)
class JoinResult:
    joined: bool
    live: bool
    call_id: str
    tenant_id: UUID
    voice_model: str


_bound_transport: SessionTransport | None = None
_sessions: dict[str, HeldSession] = {}


def bind_session_transport(transport: SessionTransport | None) -> SessionTransport | None:
    """Tests inject FakeSessionTransport here. Production does not."""
    global _bound_transport
    previous = _bound_transport
    _bound_transport = transport
    return previous


def reset_sessions() -> None:
    _sessions.clear()


def held_session(call_id: str) -> HeldSession | None:
    return _sessions.get(call_id)


def _transport() -> SessionTransport:
    if _bound_transport is not None:
        return _bound_transport
    return WebsocketSessionTransport()


def _mcp_public_url() -> str:
    return mcp_public_url() or DEFAULT_MCP_URL


def _xai_api_key() -> str | None:
    value = os.environ.get("XAI_API_KEY")
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def session_instructions(packet: ShopPacket) -> str:
    """Shop facts plus our template. No dollar figures. An LLM does not write this."""
    start = packet.after_hours_start.strftime("%H:%M")
    end = packet.after_hours_end.strftime("%H:%M")
    zips = ", ".join(packet.service_area_zips) if packet.service_area_zips else "none listed"
    notes = packet.greeting_notes or "none"
    body = (
        f"{MABEL_VOICE_AGENT_TEMPLATE.instructions} "
        f"Shop: {packet.name}. Trade: {packet.vertical}. "
        f"Timezone: {packet.timezone}. "
        f"After hours: {start} to {end}. "
        f"Service-area zips: {zips}. "
        f"Greeting notes: {notes}. "
        "Never quote a price. Never invent an arrival time. "
        "Never say an estimate range or an hourly rate."
    )
    reject_dollar_text(body, field="session instructions")
    return body


def build_session_update(packet: ShopPacket, *, mcp_token: str) -> dict[str, Any]:
    instructions = session_instructions(packet)
    payload = {
        "type": "session.update",
        "session": {
            "voice": SESSION_VOICE,
            "instructions": instructions,
            "turn_detection": {"type": "server_vad"},
            "audio": {
                "input": {"format": {"type": "audio/pcmu"}},
                "output": {"format": {"type": "audio/pcmu"}},
            },
            "tools": [
                {
                    "type": "mcp",
                    "server_label": "mabel",
                    "server_url": _mcp_public_url(),
                    "authorization": f"Bearer {mcp_token}",
                    "allowed_tools": list(MABEL_MCP_TOOLS),
                }
            ],
        },
    }
    reject_dollar_text(json.dumps(payload), field="session.update")
    return payload


def build_force_message() -> dict[str, Any]:
    return {
        "type": "conversation.item.create",
        "item": {
            "type": "force_message",
            "role": "assistant",
            "interruptible": False,
            "content": [{"type": "output_text", "text": OPENING_DISCLOSURE}],
        },
    }


async def join_inbound_call(
    *,
    tenant_id: UUID,
    call_id: str,
    packet: ShopPacket | None = None,
) -> JoinResult:
    """Handshake only. Does not take an agent live. Does not send response.create."""
    from mabel.voice.webhook import AGENT_LIVE

    if AGENT_LIVE:
        raise SessionError("Mabel will not take an agent live from this path.")
    shop = packet if packet is not None else packet_for(tenant_id)
    if shop is None:
        raise PacketError("Mabel has no shop packet for this tenant.")
    key = _xai_api_key()
    if not key:
        raise SessionError("Mabel cannot join this call. xAI is not configured.")
    from mabel.mcp.tokens import mint_tenant_token

    mcp_token = mint_tenant_token(tenant_id)
    update = build_session_update(shop, mcp_token=mcp_token)
    force = build_force_message()
    transport = _transport()
    await transport.join(call_id=call_id, api_key=key)
    await transport.send(update)
    await transport.send(force)
    _sessions[call_id] = HeldSession(call_id=call_id, tenant_id=tenant_id, transport=transport)
    joined = bool(transport.in_memory)
    return JoinResult(
        joined=joined,
        live=False,
        call_id=call_id,
        tenant_id=tenant_id,
        voice_model=VOICE_MODEL,
    )


def finish_session(call_id: str, *, transcript: str = "") -> CallArchive:
    """Hangup archives on our side. Tests call this. Production hangup does too."""
    held = _sessions.pop(call_id, None)
    if held is None:
        raise SessionError("Mabel has no session for this call.")
    if hasattr(held.transport, "closed"):
        held.transport.closed = True  # type: ignore[attr-defined]
    text = transcript if transcript.strip() else "Mabel took this call. Transcript not yet copied."
    return archive_call(tenant_id=held.tenant_id, call_id=call_id, transcript=text)
