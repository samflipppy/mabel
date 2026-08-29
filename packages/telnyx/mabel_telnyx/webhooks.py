"""Verifying an inbound Telnyx webhook.

Telnyx signs with **Ed25519**, not HMAC — a different scheme from xAI's, and
the reason this is a separate module rather than a parameter to one shared
verifier. Two signature schemes behind one function is how one of them ends up
silently unverified.

Headers: `telnyx-signature-ed25519` and `telnyx-timestamp`. The signed payload
is `{timestamp}|{raw_body}`, with a pipe, not a dot.

Same three rules as every webhook here (invariant 8): verify against the raw
body, reject old timestamps, and be idempotent on the event id.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Telnyx's documented tolerance.
MAX_AGE_SECONDS = 300
MAX_FUTURE_SKEW_SECONDS = 60


class TelnyxWebhookError(Exception):
    """The request is not one we should act on."""


class PublicKeyUnavailable(TelnyxWebhookError):
    """No public key configured. An unverifiable webhook is refused, never
    processed. See docs/BLOCKED.md #3."""


@dataclass(frozen=True, slots=True)
class VerifiedWebhook:
    event_id: str
    event_type: str
    timestamp: int


def public_key() -> str:
    key = os.environ.get("TELNYX_PUBLIC_KEY")
    if not key:
        raise PublicKeyUnavailable(
            "TELNYX_PUBLIC_KEY is unset. An unverified webhook is not processed. "
            "See docs/BLOCKED.md #3."
        )
    return key


def signed_payload(timestamp: str, raw_body: bytes) -> bytes:
    """`{timestamp}|{body}`. A pipe, not a dot — Telnyx differs from Standard
    Webhooks here, and using the wrong separator fails every signature with no
    useful clue why."""
    return timestamp.encode() + b"|" + raw_body


def verify(
    raw_body: bytes,
    headers: dict[str, str],
    *,
    key: str | None = None,
    now: float | None = None,
) -> None:
    """Verify, or raise.

    `raw_body` must be the bytes as received. A JSON round trip changes key
    order and whitespace, and the signature is over bytes.
    """
    if not isinstance(raw_body, bytes | bytearray):
        raise TelnyxWebhookError(
            "raw_body must be bytes. Re-serialising the JSON breaks the signature."
        )

    lowered = {k.lower(): v for k, v in headers.items()}
    signature = lowered.get("telnyx-signature-ed25519")
    timestamp = lowered.get("telnyx-timestamp")
    if not signature or not timestamp:
        raise TelnyxWebhookError("missing telnyx-signature-ed25519 or telnyx-timestamp")

    try:
        sent_at = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise TelnyxWebhookError(f"telnyx-timestamp is not an integer: {timestamp!r}") from exc

    current = time.time() if now is None else now
    age = current - sent_at
    if age > MAX_AGE_SECONDS:
        raise TelnyxWebhookError(f"webhook is {int(age)}s old; an old signature is replayable")
    if age < -MAX_FUTURE_SKEW_SECONDS:
        raise TelnyxWebhookError(f"webhook is {int(-age)}s in the future; check the clock")

    resolved = key if key is not None else public_key()

    try:
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise TelnyxWebhookError(
            "PyNaCl is required to verify Telnyx signatures. Without it we cannot "
            "verify, so we refuse rather than accept."
        ) from exc

    try:
        verifier = VerifyKey(base64.b64decode(resolved))
        verifier.verify(signed_payload(timestamp, bytes(raw_body)), base64.b64decode(signature))
    except BadSignatureError as exc:
        logger.warning(
            "telnyx webhook signature mismatch",
            extra={"body_bytes": len(raw_body), "timestamp": timestamp},
        )
        raise TelnyxWebhookError("telnyx webhook signature did not match") from exc
    except Exception as exc:  # noqa: BLE001 - malformed key or signature
        raise TelnyxWebhookError(
            f"could not verify the telnyx signature: {type(exc).__name__}"
        ) from exc
