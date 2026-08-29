"""The fail-closed gate in front of `session.py`.

03-VOICE.md: verify the signed `realtime.call.incoming` webhook, resolve the
tenant from the To-DID, then hand off. The socket is Sam's. This file never
opens one.

Order is the security design:

1. Read the raw body. Never a re-serialised JSON copy.
2. Verify the Standard Webhooks signature and reject anything older than 300s.
3. Idempotency on `webhook-id`. A retry must not open a second session.
4. Resolve the tenant from the dialed number, server-side, before any join.
5. Unknown DID refuses. The call falls through to carrier voicemail.
6. Missing Telnyx or xAI secrets refuse. The app stays up; this path does not
   pretend it can answer.
7. Only then hand off to `open_session`. That function is Sam's and still
   raises. We catch that and fail closed rather than crash the process.

Nothing the model says reaches this file. The tenant is a fact by the time
anyone talks.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from mabel_domain.phone import PhoneError, normalize_e164, try_normalize_e164
from mabel_media.session import IncomingCall, open_session
from mabel_xai.client import VOICE_MODEL
from mabel_xai.webhooks import WebhookError, verify as verify_xai

logger = logging.getLogger(__name__)

# SIP To headers look like `"Name" <sip:+12165550148@sip.voice.x.ai>`.
_PLUS_E164 = re.compile(r"\+[1-9]\d{7,14}")

IDEMPOTENCY_SOURCE = "xai"

SessionOpener = Callable[[IncomingCall], Awaitable[None]]

_bound_opener: SessionOpener | None = None


def bind_inbound_opener(opener: SessionOpener | None) -> None:
    """Tests bind a fake opener so nothing here opens a socket."""
    global _bound_opener
    _bound_opener = opener


@dataclass(frozen=True, slots=True)
class InboundDecision:
    """What the gate decided. Tests assert on this rather than on logs."""

    status_code: int
    body: dict[str, Any]
    tenant_id: UUID | None = None
    call_id: str | None = None
    handed_off: bool = False


@dataclass
class _Opened:
    """Records a hand-off so a test can prove the tenant was already set."""

    calls: list[IncomingCall] = field(default_factory=list)


async def accept_inbound_call(
    raw_body: bytes,
    headers: dict[str, str],
    *,
    engine: Any | None = None,
    now: float | None = None,
    opener: SessionOpener | None = None,
) -> InboundDecision:
    """Decide whether this inbound call is ours, and whether we can answer.

    Never raises to the HTTP layer for a bad webhook, a missing key, or an
    unknown number. Those are decisions, not crashes.
    """
    if not os.environ.get("XAI_WEBHOOK_SECRET"):
        return _refuse(
            503,
            "Mabel cannot take this call. The inbound webhook is not configured.",
        )

    try:
        parsed_headers = verify_xai(raw_body, headers, now=now)
    except WebhookError as exc:
        logger.warning("rejected an inbound xAI webhook: %s", type(exc).__name__)
        return _refuse(401, "unauthorized")

    try:
        payload = _parse_body(raw_body)
        to_did = to_did_from_payload(payload)
        call_id = _call_id_from_payload(payload)
    except ValueError as exc:
        return _refuse(400, str(exc))

    if not call_id:
        return _refuse(400, "Mabel cannot find this call.")

    if engine is None:
        try:
            from mabel_db.tenant import DatabaseUnavailable, database_url

            database_url()
        except DatabaseUnavailable:
            return _refuse(
                503,
                "Mabel cannot take this call. The database is not configured.",
                call_id=call_id,
            )

    if not await _first_time(parsed_headers.webhook_id, engine=engine):
        return InboundDecision(
            status_code=200,
            body={"status": "duplicate", "call_id": call_id},
            call_id=call_id,
        )

    tenant = await _resolve_tenant(to_did, engine=engine)
    if tenant is None:
        # Unknown number. The carrier's voicemail is the correct next hop,
        # not somebody else's Mabel.
        return InboundDecision(
            status_code=404,
            body={
                "accepted": False,
                "tenant_resolved": False,
                "joined": False,
                "reason": "Mabel does not know this number.",
                "call_id": call_id,
                "voice_model": VOICE_MODEL,
            },
            call_id=call_id,
        )

    missing = _live_path_gap()
    if missing:
        return InboundDecision(
            status_code=503,
            body={
                "accepted": False,
                "tenant_resolved": True,
                "joined": False,
                "reason": missing,
                "call_id": call_id,
                "voice_model": VOICE_MODEL,
                "live": False,
            },
            tenant_id=tenant["tenant_id"],
            call_id=call_id,
        )

    incoming = IncomingCall(
        call_id=call_id,
        tenant_id=tenant["tenant_id"],
        location_id=tenant.get("location_id"),
        from_e164=try_normalize_e164(_from_number(payload)),
        to_e164=to_did,
        received_at=datetime.now(UTC),
    )

    chosen = opener if opener is not None else _bound_opener
    try:
        if chosen is not None:
            await chosen(incoming)
        else:
            await _hand_off_to_session(incoming)
    except NotImplementedError:
        # session.py is Sam's. Fail closed so the call falls through to
        # voicemail rather than answering badly or crashing the process.
        logger.info("live SIP join is not wired; refusing call %s", call_id)
        return InboundDecision(
            status_code=503,
            body={
                "accepted": False,
                "tenant_resolved": True,
                "joined": False,
                "reason": (
                    "Mabel cannot join this call. The live SIP path is not wired."
                ),
                "call_id": call_id,
                "voice_model": VOICE_MODEL,
                "live": False,
            },
            tenant_id=incoming.tenant_id,
            call_id=call_id,
        )
    except Exception:  # noqa: BLE001 - a join failure must not take the process down
        logger.exception("refusing call %s after the session opener failed", call_id)
        return InboundDecision(
            status_code=503,
            body={
                "accepted": False,
                "tenant_resolved": True,
                "joined": False,
                "reason": "Mabel cannot join this call.",
                "call_id": call_id,
                "voice_model": VOICE_MODEL,
                "live": False,
            },
            tenant_id=incoming.tenant_id,
            call_id=call_id,
        )

    return InboundDecision(
        status_code=200,
        body={
            "accepted": True,
            "tenant_resolved": True,
            "joined": True,
            "call_id": call_id,
            "voice_model": VOICE_MODEL,
            "live": False,
        },
        tenant_id=incoming.tenant_id,
        call_id=call_id,
        handed_off=True,
    )


def to_did_from_payload(payload: dict[str, Any]) -> str:
    """The dialed number. From the payload, never from a tool argument."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    raw = payload.get("to") or (data or {}).get("to")
    if not raw:
        headers = (data or {}).get("sip_headers") or payload.get("sip_headers") or []
        raw = _sip_header(headers, "To")
    if not raw:
        raise ValueError("Mabel cannot find the number this call came in on.")
    return _extract_did(str(raw))


def _extract_did(raw: str) -> str:
    found = _PLUS_E164.search(raw)
    candidate = found.group(0) if found else raw
    try:
        return normalize_e164(candidate)
    except PhoneError as exc:
        raise ValueError("Mabel cannot find the number this call came in on.") from exc


def _sip_header(headers: list[Any], name: str) -> str | None:
    want = name.casefold()
    for item in headers:
        if isinstance(item, dict) and str(item.get("name", "")).casefold() == want:
            return str(item.get("value") or "") or None
    return None


def _call_id_from_payload(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return str(payload.get("call_id") or (data or {}).get("call_id") or "").strip()


def _from_number(payload: dict[str, Any]) -> str | None:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    raw = payload.get("from") or (data or {}).get("from")
    if raw:
        return str(raw)
    headers = (data or {}).get("sip_headers") or payload.get("sip_headers") or []
    return _sip_header(headers, "From")


def _parse_body(raw_body: bytes) -> dict[str, Any]:
    if not isinstance(raw_body, bytes | bytearray):
        raise ValueError("Mabel could not read this call payload.")
    try:
        payload = json.loads(bytes(raw_body).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Mabel could not read this call payload.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Mabel could not read this call payload.")
    return payload


def _live_path_gap() -> str | None:
    """Why we cannot join, or None when the accounts are in place.

    Fly is infrastructure, not a per-call secret — missing `FLY_*` does not
    refuse a call. Telnyx and xAI do: without them there is no trunk and no
    voice, and answering anyway would be worse than voicemail.
    """
    if not os.environ.get("TELNYX_API_KEY"):
        return "Mabel cannot take this call. Telnyx is not configured."
    if not os.environ.get("XAI_API_KEY"):
        return "Mabel cannot join this call. xAI is not configured."
    return None


def _refuse(
    status_code: int, reason: str, *, call_id: str | None = None
) -> InboundDecision:
    body: dict[str, Any] = {
        "accepted": False,
        "tenant_resolved": False,
        "joined": False,
        "reason": reason,
        "voice_model": VOICE_MODEL,
        "live": False,
    }
    if call_id:
        body["call_id"] = call_id
    return InboundDecision(status_code=status_code, body=body, call_id=call_id)


async def _resolve_tenant(did_e164: str, *, engine: Any) -> dict[str, Any] | None:
    from mabel_db.queries.config import tenant_by_did
    from mabel_db.tenant import admin_scope

    async with admin_scope(reason="resolve inbound DID", engine=engine) as conn:
        return await tenant_by_did(conn, did_e164)


async def _first_time(webhook_id: str, *, engine: Any) -> bool:
    from mabel_db.tenant import admin_scope
    from sqlalchemy import text

    if not webhook_id:
        return True

    async with admin_scope(reason="webhook idempotency", engine=engine) as conn:
        result = await conn.execute(
            text(
                "INSERT INTO webhook_receipts (webhook_id, source) VALUES (:id, :source) "
                "ON CONFLICT (webhook_id) DO NOTHING RETURNING webhook_id"
            ),
            {"id": webhook_id, "source": IDEMPOTENCY_SOURCE},
        )
        return result.first() is not None


async def _hand_off_to_session(call: IncomingCall) -> None:
    """session.py is Sam's. We call it; we do not implement it."""
    await open_session(call, _NoSocket())


class _NoSocket:
    """A transport that refuses to exist.

    The production websocket is Sam's. Handing `open_session` a fake that
    records messages would look like a join. Refusing here keeps the
    surrounding path honest: tenant resolved, socket not opened.
    """

    async def send(self, payload: dict[str, Any]) -> None:
        del payload
        raise NotImplementedError("live SIP join is Sam's")

    async def receive(self) -> dict[str, Any]:
        raise NotImplementedError("live SIP join is Sam's")

    async def close(self) -> None:
        return None
