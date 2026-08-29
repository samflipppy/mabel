"""Verifying that a webhook really came from xAI.

xAI sends the Standard Webhooks header trio: `webhook-id`, `webhook-timestamp`,
`webhook-signature`. Invariant 8 is about three things, and all three are easy
to get subtly wrong:

**Verify against the raw body.** Not the parsed JSON, not a re-serialised copy
of it. `json.dumps(json.loads(body))` reorders keys and changes whitespace, and
the signature is over bytes. If you find yourself with a `dict` here, you have
already lost the thing you needed.

**Reject old timestamps.** 300 seconds. A signature stays valid forever
otherwise, so a captured request can be replayed at leisure.

**Be idempotent.** `webhook-id` is the key, held for 10 minutes. xAI retries,
and a retried `realtime.call.incoming` that opens a second session on the same
call is two Mabels talking over each other.

The signature scheme itself is an **assumption** — see `docs/xai_notes.md` A2
and A3. It is the Standard Webhooks construction, which is what the header
names imply, but we have not seen a real signed request. Everything here is
written so the first real webhook tells us plainly whether we were right.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Standard Webhooks. Older than this and we refuse, however good the signature.
MAX_AGE_SECONDS = 300
# A little slack for clock skew between us and xAI. Without it a webhook sent
# one second in our past-future is rejected on a healthy system.
MAX_FUTURE_SKEW_SECONDS = 60


class WebhookError(Exception):
    """The request is not one we should act on."""


class SignatureMismatch(WebhookError):
    pass


class TimestampOutOfRange(WebhookError):
    pass


class SecretUnavailable(WebhookError):
    """No signing secret configured. We fail closed: an unverifiable webhook
    is refused, never processed. See docs/BLOCKED.md #5."""


@dataclass(frozen=True, slots=True)
class WebhookHeaders:
    webhook_id: str
    timestamp: str
    signature: str

    @classmethod
    def from_mapping(cls, headers: dict[str, str]) -> WebhookHeaders:
        """Header names are case-insensitive over the wire, and every framework
        normalises them differently."""
        lowered = {k.lower(): v for k, v in headers.items()}
        missing = [
            name
            for name in ("webhook-id", "webhook-timestamp", "webhook-signature")
            if name not in lowered
        ]
        if missing:
            raise WebhookError(f"missing Standard Webhooks headers: {missing}")
        return cls(
            webhook_id=lowered["webhook-id"],
            timestamp=lowered["webhook-timestamp"],
            signature=lowered["webhook-signature"],
        )


def signing_secret(env_var: str = "XAI_WEBHOOK_SECRET") -> str:
    secret = os.environ.get(env_var)
    if not secret:
        raise SecretUnavailable(
            f"{env_var} is unset. An unverified webhook is not processed. See docs/BLOCKED.md #5."
        )
    return secret


def _decode_secret(secret: str) -> list[bytes]:
    """Candidate key encodings.

    # ASSUMPTION (docs/xai_notes.md A3): Standard Webhooks specifies a
    # `whsec_`-prefixed base64 secret. We have not seen xAI's. Rather than
    # guessing one and being silently wrong, we try the documented form and the
    # raw bytes, and the first real webhook tells us which it is. Trying two
    # candidate keys does not weaken anything: both are the same secret, and an
    # attacker without it still cannot produce either digest.
    """
    candidates: list[bytes] = []
    body = secret[len("whsec_") :] if secret.startswith("whsec_") else secret
    with contextlib.suppress(ValueError, binascii.Error):
        candidates.append(base64.b64decode(body, validate=True))
    candidates.append(body.encode())
    if body != secret:
        candidates.append(secret.encode())
    return candidates


def signed_payload(webhook_id: str, timestamp: str, raw_body: bytes) -> bytes:
    """`{id}.{timestamp}.{body}` — the Standard Webhooks construction.

    # ASSUMPTION (docs/xai_notes.md A2).
    """
    return b".".join([webhook_id.encode(), timestamp.encode(), raw_body])


def expected_signatures(secret: str, webhook_id: str, timestamp: str, raw_body: bytes) -> list[str]:
    payload = signed_payload(webhook_id, timestamp, raw_body)
    return [
        base64.b64encode(hmac.new(key, payload, hashlib.sha256).digest()).decode()
        for key in _decode_secret(secret)
    ]


def _offered_signatures(header: str) -> list[str]:
    """The header may carry several space-separated signatures, each prefixed
    with a version — `v1,<base64> v1,<base64>` — because a secret being rotated
    means both the old and the new one are briefly valid."""
    offered: list[str] = []
    for part in header.split():
        offered.append(part.split(",", 1)[1] if "," in part else part)
    return offered


def verify(
    raw_body: bytes,
    headers: dict[str, str],
    *,
    secret: str | None = None,
    now: float | None = None,
) -> WebhookHeaders:
    """Verify a webhook, or raise. Returns the parsed headers on success.

    `raw_body` must be the bytes as received. If it has been through a JSON
    round trip, this will fail and it should.
    """
    if not isinstance(raw_body, bytes | bytearray):
        raise WebhookError(
            "raw_body must be bytes. Re-serialising the JSON breaks the signature: "
            "key order and whitespace are part of what was signed."
        )

    parsed = WebhookHeaders.from_mapping(headers)
    resolved = secret if secret is not None else signing_secret()

    try:
        sent_at = int(parsed.timestamp)
    except (TypeError, ValueError) as exc:
        raise TimestampOutOfRange(
            f"webhook-timestamp is not an integer: {parsed.timestamp!r}"
        ) from exc

    current = time.time() if now is None else now
    age = current - sent_at
    if age > MAX_AGE_SECONDS:
        raise TimestampOutOfRange(
            f"webhook is {int(age)}s old, limit is {MAX_AGE_SECONDS}s. "
            "An old signature is a replayable one."
        )
    if age < -MAX_FUTURE_SKEW_SECONDS:
        raise TimestampOutOfRange(f"webhook is {int(-age)}s in the future; check the clock")

    expected = expected_signatures(resolved, parsed.webhook_id, parsed.timestamp, bytes(raw_body))
    offered = _offered_signatures(parsed.signature)

    for candidate in expected:
        for given in offered:
            # compare_digest, not ==. A timing side channel here leaks the
            # signature a byte at a time.
            if hmac.compare_digest(candidate, given):
                return parsed

    # Log enough to debug the assumption in A2/A3 without ever logging the
    # secret or a valid signature an attacker could reuse.
    logger.warning(
        "webhook signature mismatch",
        extra={
            "webhook_id": parsed.webhook_id,
            "offered_count": len(offered),
            "expected_count": len(expected),
            "body_bytes": len(raw_body),
            "expected_prefixes": [c[:6] for c in expected],
            "offered_prefixes": [g[:6] for g in offered],
        },
    )
    raise SignatureMismatch(
        "webhook signature did not match. If this is the first live webhook, the "
        "signing construction in docs/xai_notes.md A2 is wrong and needs correcting "
        "there before anything else."
    )
