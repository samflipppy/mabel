"""Standard Webhooks verification for xAI realtime.call.incoming.

Headers: webhook-id, webhook-timestamp, webhook-signature.
Never log the signing secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

from mabel.platform.config import ConfigError


class WebhookVerificationError(ValueError):
    """The call is not signed the way Mabel expects."""


MAX_SKEW_SECONDS = 300


def _decode_secret(secret: str) -> bytes:
    raw = secret.strip()
    if raw.startswith("whsec_"):
        raw = raw[len("whsec_") :]
    try:
        return base64.b64decode(raw)
    except Exception:
        return secret.encode("utf-8")


def verify_webhook(
    *,
    webhook_id: str | None,
    webhook_timestamp: str | None,
    webhook_signature: str | None,
    body: bytes,
    secret: str,
    now: int | None = None,
) -> None:
    if not webhook_id or not webhook_timestamp or not webhook_signature:
        raise WebhookVerificationError("Mabel cannot verify this call. Missing signature headers.")
    try:
        timestamp = int(webhook_timestamp)
    except ValueError as exc:
        raise WebhookVerificationError("Mabel cannot verify this call. Bad timestamp.") from exc
    current = int(time.time() if now is None else now)
    if abs(current - timestamp) > MAX_SKEW_SECONDS:
        raise WebhookVerificationError("Mabel cannot verify this call. Signature is stale.")

    signed = f"{webhook_id}.{webhook_timestamp}.".encode("utf-8") + body
    expected = hmac.new(_decode_secret(secret), signed, hashlib.sha256).digest()
    expected_b64 = base64.b64encode(expected).decode("ascii")

    candidates = []
    for part in webhook_signature.split(" "):
        if part.startswith("v1,"):
            candidates.append(part[3:])
        elif "," in part:
            version, value = part.split(",", 1)
            if version == "v1":
                candidates.append(value)
    if not candidates:
        raise WebhookVerificationError("Mabel cannot verify this call. No v1 signature.")

    matched = False
    for candidate in candidates:
        try:
            given = base64.b64decode(candidate)
        except Exception:
            continue
        if hmac.compare_digest(given, expected):
            matched = True
            break
        # Some senders compare the base64 string.
        if hmac.compare_digest(candidate.encode("ascii"), expected_b64.encode("ascii")):
            matched = True
            break
    if not matched:
        raise WebhookVerificationError("Mabel cannot verify this call. Bad signature.")


def sign_webhook(*, webhook_id: str, webhook_timestamp: str, body: bytes, secret: str) -> str:
    """Test helper. Not used on the call path."""
    signed = f"{webhook_id}.{webhook_timestamp}.".encode("utf-8") + body
    digest = hmac.new(_decode_secret(secret), signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode("ascii")


def missing_secret_error() -> ConfigError:
    return ConfigError("Mabel cannot verify this call. Webhook signing is not configured.")
