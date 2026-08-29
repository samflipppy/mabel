"""Call Control events, for the calls Mabel never got to answer.

A separate module from `telnyx.py` on purpose. That file handles messaging and
is settled; this one is built on four documented-but-unverified assumptions
(docs/telnyx_notes.md, T-1 to T-4) and will change when the account exists.
Keeping them apart means the churn does not touch the signature verification.

**What this is for.** A caller who hangs up while it is ringing leaves no
trace: no lead, no transcript, no name. The business does not know they
existed. There is a number, and about ninety seconds in which a text still
reads as service rather than cold outreach. Podium and Weave are businesses
built on this one message.

**The default is "do nothing".** A missed call has to be positively identified
-- an inbound call, whose leg we saw hang up, and never saw answered. Treating
"we did not see an answer" as missed would text every caller who spoke to
Mabel for four minutes, because a dropped or reordered `call.answered` webhook
would look identical. So an unrecognised event shape sends nothing, and the
feature simply does not work until the assumptions are checked. That is the
correct failure: a missed-call text that does not send is a feature that is
off, and one sent to the wrong person is a stranger hearing from a business
they never called.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from mabel_db.queries import contacts as contacts_q
from mabel_db.queries.customer_sms import enqueue_to_customer, may_text, record_consent
from mabel_db.tenant import admin_scope, tenant_scope
from mabel_domain.phone import PhoneError, normalize_e164
from mabel_sms.customer import missed_call
from mabel_telnyx.webhooks import TelnyxWebhookError
from mabel_telnyx.webhooks import verify as verify_telnyx
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter()

# ASSUMPTION (T-1): these are the event names. See docs/telnyx_notes.md.
ANSWERED = "call.answered"
HANGUP = "call.hangup"

# ASSUMPTION (T-2): an inbound call to our DID is marked this way.
INBOUND = "incoming"


@router.post("/webhooks/telnyx/call")
async def call_event(request: Request) -> JSONResponse:
    """Always 200 once the signature verifies, like the messaging webhook.

    A retry storm caused by our own bug turns one missed call into a hundred
    texts, which is the exact failure this feature is most likely to cause.
    """
    raw = await request.body()
    try:
        verify_telnyx(raw, dict(request.headers))
    except TelnyxWebhookError as exc:
        logger.warning("rejected a telnyx call webhook: %s", exc)
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    payload = await _parse(raw)
    if payload is None:
        return JSONResponse(status_code=400, content={"error": "malformed body"})

    event = payload.get("data", {})
    event_type = str(event.get("event_type") or "")
    body = event.get("payload") or {}

    # Everything received, logged at INFO, so the assumptions above can be
    # checked against reality in ten minutes once an account exists rather
    # than by reading Telnyx's documentation again.
    logger.info("telnyx call event %r with payload keys %s", event_type, sorted(body.keys()))

    event_id = str(event.get("id") or "")
    if event_id and not await _first_time(event_id):
        return JSONResponse(content={"status": "duplicate"})

    if event_type == ANSWERED:
        await _remember_answered(body)
        return JSONResponse(content={"status": "ok", "action": "noted_answer"})

    if event_type != HANGUP:
        return JSONResponse(content={"status": "ignored", "reason": "not a hangup"})

    sent = await _text_back_if_missed(body)
    return JSONResponse(
        content={"status": "ok", "action": "missed_call_text" if sent else "no_action"}
    )


async def _text_back_if_missed(body: dict[str, Any], *, engine: Any = None) -> bool:
    """The whole decision, and every branch of it refuses.

    Five conditions have to hold before a stranger's phone buzzes. Each one is
    a way this feature could text the wrong person, and none of them defaults
    to yes.

    `engine` is injected the same way `postcall.finalize` takes one, so this is
    reachable from a test without a live `DATABASE_URL`. The route passes
    nothing and gets the process default.
    """
    if str(body.get("direction") or "") != INBOUND:
        # ASSUMPTION (T-2). Without this a callback the owner placed through
        # us would text the customer "sorry we missed your call".
        return False

    leg = _leg_id(body)
    if leg is None:
        # ASSUMPTION (T-4). No correlation id means we cannot know whether the
        # call was answered, and "cannot know" resolves to no message.
        logger.info("call hangup with no correlatable leg id; sending nothing")
        return False

    if await _was_answered(leg, engine=engine):
        return False

    from_number = _number(body.get("from"))
    to_number = _number(body.get("to"))
    if not from_number or not to_number:
        return False

    async with admin_scope(reason="attribute a missed call", engine=engine) as conn:
        did = await conn.execute(
            text("SELECT tenant_id FROM resolve_tenant_by_did(:did)"), {"did": to_number}
        )
        row = did.mappings().one_or_none()
    if row is None:
        return False

    tenant_id = row["tenant_id"]
    async with tenant_scope(tenant_id, engine=engine) as conn:
        # A missed call creates the contact. It is the only record that this
        # person ever tried to reach the business, and without it the owner
        # sees nothing in the portal either.
        contact, _ = await contacts_q.resolve_or_create(
            conn, tenant_id=tenant_id, phone_e164=from_number
        )
        await record_consent(conn, contact.id)

        decision = await may_text(conn, contact.id)
        if not decision.allowed:
            return False

        queued = await enqueue_to_customer(
            conn,
            tenant_id=tenant_id,
            contact_id=contact.id,
            kind="customer_missed_call",
            body=missed_call(
                business_name=decision.business_name or "",
                first_contact=decision.first_contact,
            ),
        )
    return queued is not None


async def _remember_answered(body: dict[str, Any], *, engine: Any = None) -> None:
    """Note that this leg was picked up, so its hangup is not a missed call.

    In `call_legs` and not `webhook_receipts`, which was the first attempt and
    was wrong: that table is pruned every ten minutes, so an eleven-minute call
    would lose its answer marker before the hangup arrived and the caller would
    be texted "sorry we missed your call" after talking to Mabel for eleven
    minutes. The bug would only have shown up on long calls, which are the good
    ones. See revision 0008.
    """
    leg = _leg_id(body)
    if leg is None:
        return
    async with admin_scope(reason="note an answered call leg", engine=engine) as conn:
        await conn.execute(
            text(
                "INSERT INTO call_legs (leg_id, answered_at) VALUES (:leg, now()) "
                "ON CONFLICT (leg_id) DO UPDATE SET answered_at = coalesce("
                "call_legs.answered_at, excluded.answered_at)"
            ),
            {"leg": leg},
        )


async def _was_answered(leg: str, *, engine: Any = None) -> bool:
    async with admin_scope(reason="check whether a call was answered", engine=engine) as conn:
        result = await conn.execute(
            text("SELECT 1 FROM call_legs WHERE leg_id = :leg AND answered_at IS NOT NULL"),
            {"leg": leg},
        )
        return result.first() is not None


def _leg_id(body: dict[str, Any]) -> str | None:
    """ASSUMPTION (T-4): one of these correlates the events of a single call."""
    for key in ("call_leg_id", "call_session_id", "call_control_id"):
        value = body.get(key)
        if value:
            return str(value)
    return None


def _number(raw: Any) -> str | None:
    if isinstance(raw, dict):
        raw = raw.get("phone_number")
    if not raw:
        return None
    try:
        return normalize_e164(str(raw))
    except PhoneError:
        return None


async def _first_time(event_id: str) -> bool:
    async with admin_scope(reason="call webhook idempotency", engine=None) as conn:
        result = await conn.execute(
            # `webhook_id` is the primary key and is global across sources,
            # so the source is part of the value rather than only a column.
            text(
                "INSERT INTO webhook_receipts (webhook_id, source) "
                "VALUES (:id, 'telnyx_call') ON CONFLICT DO NOTHING RETURNING 1"
            ),
            {"id": f"telnyx_call:{event_id}"},
        )
        return result.first() is not None


async def _parse(raw: bytes) -> dict[str, Any] | None:
    import json

    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
