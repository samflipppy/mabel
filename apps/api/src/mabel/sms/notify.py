"""Owner emergency SMS. One text, to the shop packet's owner number only.

The body is a fixed template. An LLM does not write it. Dollar-looking text
is rejected before send. The lead is already saved by the caller; missing
Telnyx must not raise in a way that loses that lead.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from mabel.platform.config import telnyx_from_e164, telnyx_ready
from mabel.shops.packet import PacketError, packet_for, reject_dollar_text
from mabel.sms.client import SmsError, TelnyxHttpSmsClient, TelnyxSmsClient

# 10DLC: nothing goes to a real number until the campaign clears.
EMERGENCY_SMS_TEMPLATE = (
    "{shop_name} emergency {trigger}. "
    "Address: {address}. "
    "Problem: {problem}. "
    "Callback: {callback}."
)

REASON_TELNYX = "telnyx not configured"
REASON_DOLLAR = "dollar-looking text"
REASON_NO_PACKET = "no shop packet"
REASON_RECAP = "recap_7am"
REASON_FROM = "from cannot be the caller"
REASON_FAILED = "sms failed"


@dataclass(frozen=True)
class SmsAttempt:
    tenant_id: UUID
    to: str
    body: str
    sent: bool
    reason: str | None
    purpose: str = "emergency_now"


_bound_client: TelnyxSmsClient | None = None
_attempts: list[SmsAttempt] = []


def bind_sms_client(client: TelnyxSmsClient | None) -> TelnyxSmsClient | None:
    """Tests inject FakeTelnyxSmsClient here. Production does not."""
    global _bound_client
    previous = _bound_client
    _bound_client = client
    return previous


def sms_attempts() -> list[SmsAttempt]:
    return list(_attempts)


def reset_sms() -> None:
    """Clear recorded attempts. Does not unbind the test client."""
    _attempts.clear()


def render_emergency_body(
    *,
    shop_name: str,
    trigger: str,
    address: str,
    problem: str,
    callback: str,
) -> str:
    return EMERGENCY_SMS_TEMPLATE.format(
        shop_name=shop_name,
        trigger=trigger,
        address=address,
        problem=problem,
        callback=callback,
    )


def send_owner_emergency_sms(
    *,
    tenant_id: UUID,
    trigger: str,
    address: str,
    problem: str,
    callback: str,
) -> dict[str, object]:
    """Text packet.owner_sms_e164. Never the caller's callback. Never From=customer."""
    packet = packet_for(tenant_id)
    if packet is None:
        return _result(sent=False, reason=REASON_NO_PACKET)

    to = packet.owner_sms_e164
    # Destination is the owner number from the packet. The callback is in the body only.

    body = render_emergency_body(
        shop_name=packet.name,
        trigger=trigger,
        address=address,
        problem=problem,
        callback=callback,
    )
    try:
        reject_dollar_text(body, field="owner SMS")
    except PacketError:
        _record(
            tenant_id=tenant_id,
            to=to,
            body=body,
            sent=False,
            reason=REASON_DOLLAR,
        )
        return _result(sent=False, reason=REASON_DOLLAR, to=to)

    if not telnyx_ready():
        _record(
            tenant_id=tenant_id,
            to=to,
            body=body,
            sent=False,
            reason=REASON_TELNYX,
        )
        return _result(sent=False, reason=REASON_TELNYX, to=to)

    from_e164 = telnyx_from_e164()
    if from_e164 is not None and from_e164 == callback:
        _record(
            tenant_id=tenant_id,
            to=to,
            body=body,
            sent=False,
            reason=REASON_FROM,
        )
        return _result(sent=False, reason=REASON_FROM, to=to)

    client = _client()
    try:
        client.send_sms(to=to, body=body, from_e164=from_e164)
    except SmsError:
        _record(
            tenant_id=tenant_id,
            to=to,
            body=body,
            sent=False,
            reason=REASON_FAILED,
        )
        return _result(sent=False, reason=REASON_FAILED, to=to)

    _record(tenant_id=tenant_id, to=to, body=body, sent=True, reason=None)
    return _result(sent=True, to=to)


def _client() -> TelnyxSmsClient:
    if _bound_client is not None:
        return _bound_client
    return TelnyxHttpSmsClient()


def _record(
    *,
    tenant_id: UUID,
    to: str,
    body: str,
    sent: bool,
    reason: str | None,
) -> None:
    _attempts.append(
        SmsAttempt(
            tenant_id=tenant_id,
            to=to,
            body=body,
            sent=sent,
            reason=reason,
        )
    )


def _result(*, sent: bool, reason: str | None = None, to: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"sent": sent}
    if reason is not None:
        payload["reason"] = reason
    if to is not None:
        payload["to"] = to
    return payload
