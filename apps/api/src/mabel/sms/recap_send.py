"""Send due 7am recaps. Documented entrypoint. Not a cron.

    python -m mabel.sms.recap_send

For queued recaps whose recap_at <= now, text the owner (never the customer)
with a deterministic template: overnight lead count and emergency count. No
dollar figures. An LLM does not write this.

Fail closed without Telnyx: mark unsent, keep the queue item. Tests bind
FakeTelnyxSmsClient. The production client refuses under pytest.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from mabel.leads.overnight import overnight_leads
from mabel.platform.config import load_settings, telnyx_from_e164, telnyx_ready
from mabel.shops.packet import PacketError, packet_for, reject_dollar_text
from mabel.sms.client import SmsError
from mabel.sms.notify import (
    REASON_DOLLAR,
    REASON_FAILED,
    REASON_NO_PACKET,
    REASON_TELNYX,
    record_sms_attempt,
    sms_client,
)
from mabel.sms.recap import RecapItem, recap_queue, replace_recap

RECAP_SMS_TEMPLATE = (
    "{shop_name} overnight recap: {lead_count} {lead_word}, {emergency_count} {emergency_word}."
)

PURPOSE_RECAP = "recap_7am"


@dataclass(frozen=True)
class RecapSendResult:
    tenant_id: UUID
    to: str | None
    body: str
    sent: bool
    reason: str | None
    lead_count: int
    emergency_count: int


def render_recap_body(*, shop_name: str, lead_count: int, emergency_count: int) -> str:
    """Fixed template. Counts come from our rows, not a model."""
    lead_word = "lead" if lead_count == 1 else "leads"
    emergency_word = "emergency" if emergency_count == 1 else "emergencies"
    return RECAP_SMS_TEMPLATE.format(
        shop_name=shop_name,
        lead_count=lead_count,
        lead_word=lead_word,
        emergency_count=emergency_count,
        emergency_word=emergency_word,
    )


def send_due_recaps(now: datetime) -> list[RecapSendResult]:
    """Text the owner for each shop with a due recap. Keep unsent items queued."""
    clock = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    due = _due_items(clock)
    grouped: dict[UUID, list[RecapItem]] = defaultdict(list)
    for item in due:
        grouped[item.tenant_id].append(item)

    results: list[RecapSendResult] = []
    for tenant_id, items in grouped.items():
        results.append(_send_one_shop(tenant_id, items, clock))
    return results


def _due_items(now: datetime) -> list[RecapItem]:
    found: dict[UUID, RecapItem] = {}
    for item in recap_queue():
        if item.sent_at is not None:
            continue
        if item.recap_at <= now:
            found[item.id] = item
    if _using_database():
        from mabel.sms.recap_store import load_due_recaps

        for item in load_due_recaps(now):
            found.setdefault(item.id, item)
    return list(found.values())


def _send_one_shop(tenant_id: UUID, items: list[RecapItem], now: datetime) -> RecapSendResult:
    packet = packet_for(tenant_id)
    leads = overnight_leads(tenant_id, now=now)
    lead_count = len(leads)
    emergency_count = sum(1 for lead in leads if lead.emergency_code)
    if packet is None:
        return RecapSendResult(
            tenant_id=tenant_id,
            to=None,
            body="",
            sent=False,
            reason=REASON_NO_PACKET,
            lead_count=lead_count,
            emergency_count=emergency_count,
        )

    to = packet.owner_sms_e164
    body = render_recap_body(
        shop_name=packet.name,
        lead_count=lead_count,
        emergency_count=emergency_count,
    )
    try:
        reject_dollar_text(body, field="owner SMS")
    except PacketError:
        record_sms_attempt(
            tenant_id=tenant_id,
            to=to,
            body=body,
            sent=False,
            reason=REASON_DOLLAR,
            purpose=PURPOSE_RECAP,
        )
        return _result(
            tenant_id,
            to=to,
            body=body,
            sent=False,
            reason=REASON_DOLLAR,
            lead_count=lead_count,
            emergency_count=emergency_count,
        )

    if not telnyx_ready():
        record_sms_attempt(
            tenant_id=tenant_id,
            to=to,
            body=body,
            sent=False,
            reason=REASON_TELNYX,
            purpose=PURPOSE_RECAP,
        )
        return _result(
            tenant_id,
            to=to,
            body=body,
            sent=False,
            reason=REASON_TELNYX,
            lead_count=lead_count,
            emergency_count=emergency_count,
        )

    try:
        sms_client().send_sms(to=to, body=body, from_e164=telnyx_from_e164())
    except SmsError:
        record_sms_attempt(
            tenant_id=tenant_id,
            to=to,
            body=body,
            sent=False,
            reason=REASON_FAILED,
            purpose=PURPOSE_RECAP,
        )
        return _result(
            tenant_id,
            to=to,
            body=body,
            sent=False,
            reason=REASON_FAILED,
            lead_count=lead_count,
            emergency_count=emergency_count,
        )

    _mark_sent(items, now)
    record_sms_attempt(
        tenant_id=tenant_id,
        to=to,
        body=body,
        sent=True,
        reason=None,
        purpose=PURPOSE_RECAP,
    )
    return _result(
        tenant_id,
        to=to,
        body=body,
        sent=True,
        reason=None,
        lead_count=lead_count,
        emergency_count=emergency_count,
    )


def _mark_sent(items: list[RecapItem], when: datetime) -> None:
    for item in items:
        updated = RecapItem(
            tenant_id=item.tenant_id,
            recap_at=item.recap_at,
            id=item.id,
            lead_id=item.lead_id,
            sent_at=when,
        )
        replace_recap(updated)
        if _using_database():
            from mabel.sms.recap_store import mark_recap_sent

            mark_recap_sent(updated)


def _using_database() -> bool:
    return bool(load_settings().database_url)


def _result(
    tenant_id: UUID,
    *,
    to: str | None,
    body: str,
    sent: bool,
    reason: str | None,
    lead_count: int,
    emergency_count: int,
) -> RecapSendResult:
    return RecapSendResult(
        tenant_id=tenant_id,
        to=to,
        body=body,
        sent=sent,
        reason=reason,
        lead_count=lead_count,
        emergency_count=emergency_count,
    )


def main() -> int:
    results = send_due_recaps(datetime.now(timezone.utc))
    sent = sum(1 for item in results if item.sent)
    print(f"Mabel sent {sent} recap(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
