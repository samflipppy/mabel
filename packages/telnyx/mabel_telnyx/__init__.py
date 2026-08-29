"""Telnyx: SMS today, SIP configuration when the account exists.

Two signature schemes exist in this repo and they are deliberately in separate
modules. xAI signs with HMAC-SHA256; Telnyx signs with Ed25519. One verifier
taking a scheme parameter is how one of them ends up silently unverified.
"""

from __future__ import annotations

from mabel_telnyx.client import (
    SEGMENT_CHARS,
    Client,
    FakeTelnyxClient,
    SendFailed,
    SentMessage,
    TelnyxClient,
    TelnyxRefusedUnderTest,
    TelnyxUnavailable,
    api_key,
    delivery_risk,
    segments_for,
    sip_connection_settings,
)
from mabel_telnyx.webhooks import (
    PublicKeyUnavailable,
    TelnyxWebhookError,
    VerifiedWebhook,
    signed_payload,
)
from mabel_telnyx.webhooks import verify as verify_webhook

__all__ = [
    "SEGMENT_CHARS",
    "Client",
    "FakeTelnyxClient",
    "PublicKeyUnavailable",
    "SendFailed",
    "SentMessage",
    "TelnyxClient",
    "TelnyxRefusedUnderTest",
    "TelnyxUnavailable",
    "TelnyxWebhookError",
    "VerifiedWebhook",
    "api_key",
    "delivery_risk",
    "segments_for",
    "signed_payload",
    "sip_connection_settings",
    "verify_webhook",
]
