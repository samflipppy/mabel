"""Telnyx. SMS today, SIP configuration when the account exists.

Fails closed. With no `TELNYX_API_KEY` the client refuses to construct, and the
worker records the notification as `failed` with the reason rather than
pretending it went. A recap the owner never received but that we recorded as
sent is worse than one that visibly failed. See docs/BLOCKED.md #3.

**Two account-shaped things are still missing and neither is code.** The API
key, and the 10DLC brand and campaign registration (BLOCKED.md #4). Without
the second, carriers will filter application-to-person traffic silently — the
API accepts the message, returns an id, and nobody's phone rings. That is the
worst failure mode in the product, so `delivery_risk()` exists to say plainly
when we are in it.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from mabel_domain.phone import PhoneError, normalize_e164

logger = logging.getLogger(__name__)

API_BASE = "https://api.telnyx.com/v2"
DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# One GSM-7 segment. Longer messages are legal and cost more, and the emergency
# path never sends one.
SEGMENT_CHARS = 160


class TelnyxUnavailable(RuntimeError):
    """No API key. We refuse rather than degrade. See docs/BLOCKED.md #3."""


class TelnyxRefusedUnderTest(RuntimeError):
    """Something tried to send a real SMS from a test."""


class SendFailed(RuntimeError):
    """Telnyx rejected the message."""


def _under_pytest() -> bool:
    return "pytest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST"))


def api_key() -> str:
    key = os.environ.get("TELNYX_API_KEY")
    if not key:
        raise TelnyxUnavailable(
            "TELNYX_API_KEY is unset. Mabel does not invent a credential, and a "
            "notification we cannot send is recorded as failed rather than sent. "
            "See docs/BLOCKED.md #3."
        )
    return key


def messaging_profile_id() -> str | None:
    """Optional. Telnyx routes by profile when one is set."""
    return os.environ.get("TELNYX_MESSAGING_PROFILE_ID")


def delivery_risk() -> Literal["ok", "unregistered", "no_key"]:
    """Can a message we send actually arrive?

    `unregistered` is the dangerous state: the API accepts the message and
    returns an id, and carriers drop it. Everything looks healthy and the owner
    gets nothing. The dashboard and the runbook both surface this.
    """
    if not os.environ.get("TELNYX_API_KEY"):
        return "no_key"
    if not os.environ.get("TELNYX_10DLC_CAMPAIGN_ID"):
        # See docs/BLOCKED.md #4. Registration has weeks of lead time.
        return "unregistered"
    return "ok"


@dataclass(frozen=True, slots=True)
class SentMessage:
    provider_ref: str
    segments: int
    to_e164: str


class TelnyxClient:
    """The live client. Refuses without a key, and refuses under pytest."""

    def __init__(
        self, *, key: str | None = None, transport: httpx.AsyncBaseTransport | None = None
    ):
        if _under_pytest() and transport is None:
            raise TelnyxRefusedUnderTest(
                "TelnyxClient refuses to run under pytest. Bind FakeTelnyxClient. "
                "A test that sends a real SMS costs money and may wake somebody up."
            )
        self._key = key if key is not None else api_key()
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=DEFAULT_TIMEOUT,
            transport=transport,
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send_sms(self, *, to_e164: str, body: str, from_e164: str) -> SentMessage:
        """Send one message.

        Raises on failure rather than returning a status, because the caller is
        the worker and its retry logic keys off the exception. A silent failure
        here is a 3am emergency alert that never arrives.
        """
        try:
            destination = normalize_e164(to_e164)
            origin = normalize_e164(from_e164)
        except PhoneError as exc:
            raise SendFailed(f"not a sendable number: {exc}") from exc

        payload: dict[str, Any] = {"from": origin, "to": destination, "text": body}
        profile = messaging_profile_id()
        if profile:
            payload["messaging_profile_id"] = profile

        try:
            response = await self._client.post("/messages", json=payload)
        except httpx.HTTPError as exc:
            raise SendFailed(f"telnyx unreachable: {exc}") from exc

        if response.status_code >= 400:
            # The body can carry the destination number; the status and the
            # Telnyx error code are enough to act on.
            raise SendFailed(f"telnyx returned {response.status_code}")

        data = response.json().get("data", {})
        return SentMessage(
            provider_ref=str(data.get("id", "")),
            segments=int(data.get("parts", 1) or 1),
            to_e164=destination,
        )


@dataclass
class FakeTelnyxClient:
    """What tests and the local environment bind.

    Records what would have been sent. It is not a stub standing in for a
    credential — nothing here pretends a message was delivered, and the worker
    treats a fake send as exactly what it is.
    """

    sent: list[SentMessage] = field(default_factory=list)
    bodies: list[str] = field(default_factory=list)
    fail_with: str | None = None

    async def aclose(self) -> None:
        return None

    async def send_sms(self, *, to_e164: str, body: str, from_e164: str) -> SentMessage:
        if self.fail_with:
            raise SendFailed(self.fail_with)
        destination = normalize_e164(to_e164)
        message = SentMessage(
            provider_ref=f"fake_{len(self.sent)}",
            segments=segments_for(body),
            to_e164=destination,
        )
        self.sent.append(message)
        self.bodies.append(body)
        return message


Client = TelnyxClient | FakeTelnyxClient


def segments_for(body: str) -> int:
    """How many SMS segments this costs.

    Concatenated messages carry a header, so the per-segment budget drops from
    160 to 153 once there is more than one. Getting this wrong understates the
    bill and, more importantly, hides that a message is being split.
    """
    length = len(body)
    if length <= SEGMENT_CHARS:
        return 1
    return -(-length // 153)


def sip_connection_settings(did_e164: str) -> dict[str, Any]:
    """What the Telnyx FQDN connection needs, for the runbook and onboarding.

    Not applied by code — creating a SIP connection is an account operation
    Sam does, and nothing irreversible happens without a human. This returns
    the settings so the runbook and the portal can show them consistently.
    """
    return {
        "connection_type": "fqdn",
        "fqdn": "sip.voice.x.ai",
        "port": 5060,
        "transport": "tls",
        "origin": "byo_trunk",
        "codecs": ["G711U"],
        "inbound_uri": f"sip:{did_e164}@sip.voice.x.ai;transport=tls",
        "number_format": "e164",
    }
