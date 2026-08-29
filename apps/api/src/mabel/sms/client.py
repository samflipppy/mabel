"""Telnyx SMS client.

10DLC: nothing goes to a real number until the campaign clears.

Tests bind FakeTelnyxSmsClient. They never need a real key and they never
POST to Telnyx. The production client reads TELNYX_API_KEY from the
environment at send time and refuses to run under pytest.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

TELNYX_MESSAGES_URL = "https://api.telnyx.com/v2/messages"


class SmsError(RuntimeError):
    pass


class TelnyxSmsClient(Protocol):
    def send_sms(self, *, to: str, body: str, from_e164: str | None = None) -> None:
        """Send one SMS. Never log a key. Never From=the caller."""


@dataclass
class FakeTelnyxSmsClient:
    """In-memory stand-in. The only client pytest should use."""

    sent: list[dict[str, str | None]] = field(default_factory=list)

    def send_sms(self, *, to: str, body: str, from_e164: str | None = None) -> None:
        self.sent.append({"to": to, "body": body, "from_e164": from_e164})


class TelnyxHttpSmsClient:
    """POST /v2/messages. Key stays in the environment. Never written to a file."""

    def send_sms(self, *, to: str, body: str, from_e164: str | None = None) -> None:
        # Tests must use FakeTelnyxSmsClient. No live Telnyx calls from pytest.
        if os.environ.get("PYTEST_CURRENT_TEST"):
            raise SmsError("Mabel will not call Telnyx from tests.")
        key = _env("TELNYX_API_KEY")
        if not key:
            raise SmsError("Mabel cannot text the owner. Telnyx is not configured.")
        if not from_e164:
            raise SmsError("Mabel cannot text the owner. No From number is configured.")
        import httpx

        # Do not log headers. The bearer token is the key.
        response = httpx.post(
            TELNYX_MESSAGES_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={"to": to, "from": from_e164, "text": body},
            timeout=10.0,
        )
        if response.status_code >= 400:
            raise SmsError("Mabel could not send the owner text.")


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
