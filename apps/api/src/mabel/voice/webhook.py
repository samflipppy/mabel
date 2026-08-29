"""Inbound call webhook.

Verify the signature, resolve the tenant from the To DID, then stop if xAI
is not configured. Joining the live realtime session is a later change and
needs Sam's sign-off on that specific change.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from mabel.platform.config import ConfigError, require_webhook_secret, xai_ready
from mabel.platform.tenancy import UnknownDidError, directory
from mabel.voice.did import to_did_from_payload
from mabel.voice.model import VOICE_MODEL
from mabel.voice.signatures import WebhookVerificationError, verify_webhook

router = APIRouter()


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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not xai_ready():
        # Fail closed. Do not log anything that could be a key.
        raise HTTPException(
            status_code=503,
            detail="Mabel cannot join this call. xAI is not configured.",
        )

    # Keys are present. Still do not open the realtime socket from this stub.
    call_id = (payload.get("data") or {}).get("call_id")
    return JSONResponse(
        {
            "accepted": True,
            "tenant_resolved": True,
            "vertical": tenant.vertical,
            "voice_model": VOICE_MODEL,
            "call_id": call_id,
            "joined": False,
            "reason": "Call join is not wired yet. Sam has to approve that change.",
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
