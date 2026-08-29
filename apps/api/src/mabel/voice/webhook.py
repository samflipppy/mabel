"""Inbound call webhook.

Verify the signature, resolve the tenant from the To DID, then stop if
Telnyx or xAI is not configured. When keys are present, join through
SessionTransport: mint a tenant MCP token, send session.update from our
template plus the shop packet, then force_message disclosure. Tests bind
FakeSessionTransport. The production websocket client refuses under pytest.
AGENT_LIVE stays false. Nothing here takes a shop live.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from mabel.platform.config import ConfigError, require_webhook_secret, telnyx_ready, xai_ready
from mabel.platform.tenancy import UnknownDidError, directory
from mabel.shops.packet import PacketError
from mabel.voice.did import to_did_from_payload
from mabel.voice.model import VOICE_MODEL
from mabel.voice.session import SessionError, join_inbound_call
from mabel.voice.signatures import WebhookVerificationError, verify_webhook

router = APIRouter()

# Nothing irreversible without a human. A bot does not flip this.
AGENT_LIVE = False


@router.post("/voice/webhook")
async def inbound_call(request: Request) -> JSONResponse:
    try:
        secret = require_webhook_secret()
    except ConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    body = await request.body()
    try:
        verify_webhook(
            webhook_id=request.headers.get("webhook-id"),
            webhook_timestamp=request.headers.get("webhook-timestamp"),
            webhook_signature=request.headers.get("webhook-signature"),
            body=body,
            secret=secret,
        )
    except WebhookVerificationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        payload = await _json(body)
        to_did = to_did_from_payload(payload)
        tenant = directory().resolve(to_did)
    except UnknownDidError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PacketError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not telnyx_ready():
        raise HTTPException(
            status_code=503,
            detail="Mabel cannot take this call. Telnyx is not configured.",
        )
    if not xai_ready():
        raise HTTPException(
            status_code=503,
            detail="Mabel cannot join this call. xAI is not configured.",
        )

    if AGENT_LIVE:
        raise HTTPException(
            status_code=503,
            detail="Mabel will not take an agent live from this stub.",
        )

    call_id = str((payload.get("data") or {}).get("call_id") or "")
    if not call_id:
        raise HTTPException(status_code=400, detail="Mabel cannot find this call.")

    packet = tenant.packet
    try:
        joined = await join_inbound_call(
            tenant_id=tenant.id,
            call_id=call_id,
            packet=packet,
        )
    except ConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PacketError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SessionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return JSONResponse(
        {
            "accepted": True,
            "tenant_resolved": True,
            "vertical": tenant.vertical,
            "voice_model": VOICE_MODEL,
            "call_id": call_id,
            "joined": joined.joined,
            "live": False,
        }
    )


async def _json(body: bytes) -> dict:
    import json

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Mabel could not read this call payload.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Mabel could not read this call payload.")
    return payload
