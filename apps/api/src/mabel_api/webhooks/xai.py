"""Inbound xAI voice webhook: `realtime.call.incoming`.

The media process owns the socket. This route is the HTTP front door:
verify the raw body, resolve the tenant from the To-DID, refuse if the
accounts are not in place, and only then hand off. `open_session` is
Sam's; we never join a live SIP call from this file.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from mabel_media.inbound import accept_inbound_call

router = APIRouter()


@router.post("/webhooks/xai/inbound")
async def inbound_call(request: Request) -> JSONResponse:
    raw = await request.body()
    decision = await accept_inbound_call(raw, dict(request.headers))
    return JSONResponse(status_code=decision.status_code, content=decision.body)
