"""The single gate every customer-facing message passes through.

There is one function that queues a message to a caller, and it is this one.
Nothing else in the codebase is allowed to write a `notifications` row whose
`to_address` belongs to a contact, because the four conditions below have to be
checked together and checking them at four call sites is how three of them end
up checked at three of them.

The conditions, in the order they fail:

1. The tenant has switched customer SMS on. Off by default (revision 0007).
2. The contact still exists, is not merged away, and has a number.
3. `sms_consent_at` is set -- they called us, which is what consent is here.
4. `sms_opt_out_at` is null -- they have not said STOP.

Every refusal returns a reason rather than a bare None. The reasons end up in
the log and in `test_customer_sms_gate.py`, and a silent refusal to send is the
kind of bug that is only discovered by a customer complaining they never heard
back.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SendDecision:
    """Whether we may text this contact, and if not, why not."""

    allowed: bool
    phone_e164: str | None = None
    business_name: str | None = None
    # Set when `allowed` is False. One of: disabled, unknown, no_phone,
    # no_consent, opted_out.
    reason: str | None = None
    # False once we have texted them before, which drops the opt-out footer.
    first_contact: bool = True


async def may_text(conn: AsyncConnection, contact_id: UUID) -> SendDecision:
    """Ask before composing, so a suppressed contact costs nothing to skip.

    `first_contact` is computed from whether any customer-directed
    notification has ever been queued to this contact's number, rather than
    from a flag on the contact. A flag would have to be set by the sender and
    would therefore be wrong exactly once -- on the send that crashed between
    queueing and flagging, which is the send that then repeats the footer
    forever or drops it too early.
    """
    result = await conn.execute(
        text(
            """
            SELECT c.primary_phone,
                   c.sms_consent_at,
                   c.sms_opt_out_at,
                   t.business_name,
                   t.customer_sms_enabled
            FROM contacts c
            JOIN tenants t ON t.id = c.tenant_id
            WHERE c.id = :id
              AND c.deleted_at IS NULL
              AND c.merged_into IS NULL
            """
        ),
        {"id": contact_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return SendDecision(allowed=False, reason="unknown")

    business = row["business_name"]
    if not row["customer_sms_enabled"]:
        return SendDecision(allowed=False, reason="disabled", business_name=business)
    if not row["primary_phone"]:
        return SendDecision(allowed=False, reason="no_phone", business_name=business)
    if row["sms_opt_out_at"] is not None:
        return SendDecision(allowed=False, reason="opted_out", business_name=business)
    if row["sms_consent_at"] is None:
        return SendDecision(allowed=False, reason="no_consent", business_name=business)

    seen = await conn.execute(
        text(
            """
            SELECT 1 FROM notifications
            WHERE to_address = :phone
              AND kind IN ('customer_confirmation', 'customer_missed_call',
                           'customer_emergency', 'customer_review')
            LIMIT 1
            """
        ),
        {"phone": row["primary_phone"]},
    )
    return SendDecision(
        allowed=True,
        phone_e164=row["primary_phone"],
        business_name=business,
        first_contact=seen.first() is None,
    )


async def enqueue_to_customer(
    conn: AsyncConnection,
    *,
    tenant_id: UUID,
    contact_id: UUID,
    kind: str,
    body: str,
    lead_id: UUID | None = None,
    scheduled_for: datetime | None = None,
) -> UUID | None:
    """Queue one message to a caller, or refuse and say why.

    Re-checks the gate rather than trusting a `may_text` the caller ran
    earlier. The two are usually microseconds apart, but a review request is
    queued days after its decision was made, and in those days the contact may
    have replied STOP.
    """
    decision = await may_text(conn, contact_id)
    if not decision.allowed:
        logger.info("not texting contact %s: %s", contact_id, decision.reason)
        return None

    result = await conn.execute(
        text(
            """
            INSERT INTO notifications
              (tenant_id, kind, channel, to_address, body, lead_id, status, scheduled_for)
            VALUES
              (:tenant_id, :kind, 'sms', :to_address, :body, :lead_id, 'queued', :scheduled_for)
            RETURNING id
            """
        ),
        {
            "tenant_id": tenant_id,
            "kind": kind,
            "to_address": decision.phone_e164,
            "body": body,
            "lead_id": lead_id,
            "scheduled_for": scheduled_for,
        },
    )
    return result.scalar_one()


async def record_consent(
    conn: AsyncConnection, contact_id: UUID, *, at: datetime | None = None
) -> None:
    """They called us. That is when consent starts.

    `coalesce` keeps the *first* time rather than the most recent, because the
    question a regulator asks is when consent was obtained, and the answer
    should not move every time someone phones again.

    Never clears an opt-out. Someone who said STOP and later calls about a
    different job has consented to that call, not to being texted again -- they
    are asked in the portal instead.
    """
    await conn.execute(
        text(
            """
            UPDATE contacts
            SET sms_consent_at = coalesce(sms_consent_at, coalesce(cast(:at as timestamptz), now()))
            WHERE id = :id
            """
        ),
        {"id": contact_id, "at": at},
    )


async def opt_out(conn: AsyncConnection, contact_id: UUID, *, at: datetime | None = None) -> None:
    """STOP. Idempotent, and keeps the first timestamp for the same reason."""
    await conn.execute(
        text(
            """
            UPDATE contacts
            SET sms_opt_out_at = coalesce(sms_opt_out_at, coalesce(cast(:at as timestamptz), now()))
            WHERE id = :id
            """
        ),
        {"id": contact_id, "at": at},
    )
