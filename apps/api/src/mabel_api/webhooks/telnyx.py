"""Inbound Telnyx webhooks: owner SMS, and delivery receipts.

The order of operations here is the security design, and it is deliberate:

1. Read the **raw body**. Never the parsed JSON.
2. Verify the Ed25519 signature and the timestamp.
3. Check idempotency on the event id — Telnyx retries.
4. Honour STOP, before any tenant resolution.
5. Resolve the sender from the number, not from the body.
6. Open `tenant_scope` and act.

Step 4 sits above step 5 on purpose. An unsubscribe from a number we cannot
place is still an unsubscribe, and honouring it is a carrier obligation rather
than a feature.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from mabel_db.queries import events
from mabel_db.queries.customer_sms import opt_out, record_consent
from mabel_db.tenant import admin_scope, tenant_scope
from mabel_domain.phone import PhoneError, format_national, normalize_e164
from mabel_sms.compose import fit as fit_sms
from mabel_sms.compose import stop_confirmation, to_gsm7
from mabel_sms.intents import is_carrier_keyword
from mabel_telnyx.webhooks import TelnyxWebhookError
from mabel_telnyx.webhooks import verify as verify_telnyx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from mabel_api.sms_router import handle, resolve_sender

logger = logging.getLogger(__name__)

router = APIRouter()

# Held ten minutes, matching the prune-webhook-receipts cron and the timestamp
# tolerance. Long enough to cover every retry Telnyx will make.
IDEMPOTENCY_SOURCE = "telnyx"


@router.post("/webhooks/telnyx/sms")
async def inbound_sms(request: Request) -> JSONResponse:
    """An owner texting Mabel back.

    Always returns 200 once the signature verifies. Telnyx retries on anything
    else, and a retry storm caused by our own bug turns one confused reply into
    a hundred.
    """
    raw = await request.body()

    try:
        verify_telnyx(raw, dict(request.headers))
    except TelnyxWebhookError as exc:
        logger.warning("rejected an inbound telnyx webhook: %s", exc)
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    payload = await _parse(raw)
    if payload is None:
        return JSONResponse(status_code=400, content={"error": "malformed body"})

    event = payload.get("data", {})
    event_id = str(event.get("id") or payload.get("id") or "")
    body_text = _message_text(event)
    from_number = _from_number(event)

    if event_id and not await _first_time(event_id):
        # Telnyx retried. Replying twice to one message is worse than not
        # replying at all.
        return JSONResponse(content={"status": "duplicate"})

    if not from_number:
        return JSONResponse(content={"status": "ignored", "reason": "no sender"})

    # Carrier keywords, before anything else touches a tenant.
    if is_carrier_keyword(body_text):
        await _honour_carrier_keyword(from_number, body_text)
        return JSONResponse(content={"status": "ok", "reply": stop_confirmation()})

    async with admin_scope(reason="resolve an inbound SMS sender", engine=None) as conn:
        sender = await resolve_sender(conn, from_number)

    if sender is None:
        # Not an owner. It may still be a customer replying to the text we sent
        # after their call, which is the whole point of sending it -- "sorry we
        # missed you, reply here" that goes nowhere is worse than not sending.
        handled = await _handle_customer_reply(
            from_number=from_number, to_number=_to_number(event), body_text=body_text
        )
        if handled:
            return JSONResponse(content={"status": "ok", "action": "customer_reply"})
        # Genuinely nobody we know. Silence is right: replying tells a stranger
        # they reached something.
        logger.info("inbound SMS from an unknown number")
        return JSONResponse(content={"status": "ignored", "reason": "unknown sender"})

    async with tenant_scope(sender.tenant_id) as conn:
        reply = await handle(conn, sender, body_text)

    # The reply is queued rather than sent inline, so the webhook returns fast
    # and the send goes through the same retrying path as everything else.
    async with tenant_scope(sender.tenant_id) as conn:
        from mabel_db.queries.notifications import enqueue

        await enqueue(
            conn,
            tenant_id=sender.tenant_id,
            kind="system",
            channel="sms",
            to_address=sender.phone_e164,
            body=reply.body,
            user_id=sender.user_id,
            lead_id=reply.lead_id,
        )

    return JSONResponse(content={"status": "ok", "action": reply.action})


@router.post("/webhooks/telnyx/status")
async def delivery_status(request: Request) -> JSONResponse:
    """Delivery receipts.

    Worth wiring because of the 10DLC problem (docs/BLOCKED.md #4): an
    unregistered campaign gets messages accepted by the API and dropped by the
    carrier. The receipt is the only place that becomes visible.
    """
    raw = await request.body()
    try:
        verify_telnyx(raw, dict(request.headers))
    except TelnyxWebhookError as exc:
        logger.warning("rejected a telnyx status webhook: %s", exc)
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    payload = await _parse(raw)
    if payload is None:
        return JSONResponse(status_code=400, content={"error": "malformed body"})

    event = payload.get("data", {})
    provider_ref = str(event.get("id") or "")
    recipients = (event.get("payload") or {}).get("to") or [{}]
    status = str(recipients[0].get("status") or event.get("status") or "")

    if not provider_ref or not status:
        return JSONResponse(content={"status": "ignored"})

    # `notifications` is tenant-scoped and we do not know the tenant from a
    # provider reference. The update runs in admin_scope and therefore matches
    # zero rows under RLS — which is why the status is recorded on the audit
    # log instead, where it is cross-tenant by design.
    async with admin_scope(reason="record a delivery receipt", engine=None) as conn:
        await conn.execute(
            text(
                """
                INSERT INTO audit_log (actor_type, action, entity, before, after)
                VALUES ('system', 'sms_delivery_receipt', 'notification',
                        NULL, jsonb_build_object('provider_ref', :ref, 'status', :status))
                """
            ),
            {"ref": provider_ref, "status": status},
        )

    if status in {"delivery_failed", "delivery_unconfirmed"}:
        logger.warning(
            "carrier reported %s for %s. If this is common, check the 10DLC "
            "registration — see docs/BLOCKED.md #4.",
            status,
            provider_ref,
        )

    return JSONResponse(content={"status": "recorded"})


@router.get("/webhooks/telnyx/health")
async def health() -> PlainTextResponse:
    from mabel_telnyx.client import delivery_risk

    return PlainTextResponse(delivery_risk())


async def _parse(raw: bytes) -> dict[str, Any] | None:
    import json

    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _message_text(event: dict[str, Any]) -> str:
    payload = event.get("payload") or {}
    return str(payload.get("text") or event.get("text") or "").strip()


def _from_number(event: dict[str, Any]) -> str | None:
    payload = event.get("payload") or {}
    raw = (payload.get("from") or {}).get("phone_number") or payload.get("from")
    if not raw:
        return None
    try:
        return normalize_e164(str(raw))
    except PhoneError:
        return None


async def _handle_customer_reply(
    *, from_number: str, to_number: str | None, body_text: str, engine: Any = None
) -> bool:
    """A customer texting the business back. Returns whether it was placed.

    Three things happen and a fourth deliberately does not.

    It is recorded as an `sms_in` event against the contact, so the reply shows
    up in their thread in the portal next to the call it followed.

    The owner is texted, because a reply the owner does not see is the same as
    no reply, and the owner does not sit in the portal.

    Consent is recorded. Someone who texts us has consented at least as clearly
    as someone who called us.

    **Mabel does not answer.** No auto-reply, no acknowledgement, no "we got
    your message". Two systems that each reply to every inbound message will
    text each other until a carrier stops them, and a human answering a text
    from a customer is the product working, not a gap in it.
    """
    if not to_number or not body_text.strip():
        return False

    async with admin_scope(reason="attribute an inbound customer SMS", engine=engine) as conn:
        did = await conn.execute(
            text("SELECT tenant_id, business_name FROM resolve_tenant_by_did(:did)"),
            {"did": to_number},
        )
        row = did.mappings().one_or_none()
    if row is None:
        return False

    tenant_id = row["tenant_id"]
    async with tenant_scope(tenant_id, engine=engine) as conn:
        found = await conn.execute(
            text(
                """
                SELECT id, display_name FROM contacts
                WHERE deleted_at IS NULL AND merged_into IS NULL
                  AND (primary_phone = :phone OR :phone = ANY(phones))
                ORDER BY last_seen_at DESC LIMIT 1
                """
            ),
            {"phone": from_number},
        )
        contact = found.mappings().one_or_none()
        if contact is None:
            # Texted the business line without ever calling it. Not our loop to
            # close: there is no contact, no consent, and no call to reply
            # about. The owner's own DID is not a general-purpose inbox.
            return False

        await events.append(
            conn,
            tenant_id=tenant_id,
            kind="sms_in",
            direction="inbound",
            contact_id=contact["id"],
            body=body_text,
        )
        await record_consent(conn, contact["id"])

        who = contact["display_name"] or format_national(from_number)
        await _notify_owner_of_reply(conn, tenant_id=tenant_id, who=who, message=body_text)
    return True


async def _notify_owner_of_reply(
    conn: AsyncConnection, *, tenant_id: UUID, who: str, message: str
) -> None:
    """Tell whoever is on call that a customer wrote in.

    Reuses the on-call rotation rather than blasting every user, because this
    arrives at whatever hour the customer chose to send it, and waking four
    people for a text saying "sounds good" is how a shop turns notifications
    off entirely.

    Queued as `system`, not `emergency`: it is not one, and the kinds drive
    both the delivery priority and what the owner is allowed to mute.
    """
    from mabel_db.queries.notifications import enqueue, oncall_recipients

    body = fit_sms(to_gsm7(f"Text from {who}: {message.strip()}"))
    for person in await oncall_recipients(conn):
        await enqueue(
            conn,
            tenant_id=tenant_id,
            kind="system",
            channel="sms",
            to_address=person["phone_e164"],
            body=body,
            user_id=person["id"],
        )


def _to_number(event: dict[str, Any]) -> str | None:
    """The DID that was texted, which is what says *which business* this is for.

    An owner's inbound SMS is attributed from the sender, because an owner
    belongs to a tenant. A customer's cannot be: one homeowner may be a contact
    of two contractors who both use Mabel, and their number resolves to both.
    The number they texted resolves to exactly one, by the same unique-DID rule
    that routes an inbound call.

    Telnyx delivers `to` as a list, because an MMS can have several recipients.
    Ours never do -- each tenant has one DID -- so the first entry is the one,
    and a payload shaped otherwise is better ignored than guessed at.
    """
    payload = event.get("payload") or {}
    to = payload.get("to")
    if isinstance(to, list):
        to = to[0] if to else None
    raw = (to or {}).get("phone_number") if isinstance(to, dict) else to
    if not raw:
        return None
    try:
        return normalize_e164(str(raw))
    except PhoneError:
        return None


async def _first_time(event_id: str) -> bool:
    """Insert the id, or discover it is already there.

    `webhook_receipts` is not tenant-scoped, so this runs in admin_scope. The
    insert is the check — a separate SELECT then INSERT would race two
    simultaneous retries into both being treated as first.
    """
    async with admin_scope(reason="webhook idempotency", engine=None) as conn:
        result = await conn.execute(
            text(
                "INSERT INTO webhook_receipts (webhook_id, source) VALUES (:id, :source) "
                "ON CONFLICT (webhook_id) DO NOTHING RETURNING webhook_id"
            ),
            {"id": event_id, "source": IDEMPOTENCY_SOURCE},
        )
        return result.first() is not None


async def _honour_carrier_keyword(from_number: str, body_text: str) -> None:
    """Turn off notifications for this number, whoever it belongs to.

    Deliberately does not resolve a tenant first. If the number matches nobody,
    nothing happens and that is fine; if it matches two tenants, both are
    silenced, which is the correct reading of "stop texting me".
    """
    del body_text
    async with admin_scope(reason="honour a carrier keyword", engine=None) as conn:
        result = await conn.execute(
            text("SELECT tenant_id, user_id FROM resolve_user_by_phone(:phone)"),
            {"phone": from_number},
        )
        matches = [dict(row) for row in result.mappings()]
        # A number can be both: an owner who is also in their own contacts, or
        # a customer at one tenant and an office manager at another. Both
        # lookups run, and both act. Stopping one and not the other is the
        # failure mode that gets a campaign shut down.
        contacts = await conn.execute(
            text("SELECT tenant_id, contact_id FROM resolve_contacts_by_phone(:phone)"),
            {"phone": from_number},
        )
        contact_matches = [dict(row) for row in contacts.mappings()]

    for match in matches:
        async with tenant_scope(match["tenant_id"]) as conn:
            await conn.execute(
                text(
                    "UPDATE users SET notify_emergencies = false, notify_recap = false "
                    "WHERE id = :id"
                ),
                {"id": match["user_id"]},
            )

    for match in contact_matches:
        async with tenant_scope(match["tenant_id"]) as conn:
            await opt_out(conn, match["contact_id"])
