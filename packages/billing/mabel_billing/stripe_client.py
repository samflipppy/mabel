"""Stripe. Subscriptions, the customer portal, and webhook verification.

Fails closed. No `STRIPE_SECRET_KEY` means the client refuses to construct and
the billing screen says so; it does not fall back to a mock that reports a
healthy subscription nobody is paying for. See docs/BLOCKED.md #8.

**Amounts are integer cents in both directions.** Stripe speaks cents, we speak
cents, and there is no conversion in between. A float appearing anywhere in
this file is a bug.

**We never hold a card number.** Payment methods are collected by Stripe's own
hosted flows and the customer portal. Nothing in this repo ever sees one, which
is what keeps us out of PCI scope entirely.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.stripe.com/v1"
DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

# Stripe's documented tolerance for webhook timestamps.
WEBHOOK_TOLERANCE_SECONDS = 300


class StripeUnavailable(RuntimeError):
    """No API key. See docs/BLOCKED.md #8."""


class StripeRefusedUnderTest(RuntimeError):
    """Something tried to reach the live Stripe API from a test."""


class StripeError(RuntimeError):
    pass


class WebhookVerificationFailed(StripeError):
    pass


def _under_pytest() -> bool:
    return "pytest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST"))


def api_key() -> str:
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        raise StripeUnavailable(
            "STRIPE_SECRET_KEY is unset. Billing is not configured, and we do not "
            "pretend a subscription exists. See docs/BLOCKED.md #8."
        )
    return key


def webhook_secret() -> str:
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise StripeUnavailable(
            "STRIPE_WEBHOOK_SECRET is unset. An unverified billing webhook is "
            "refused, never processed. See docs/BLOCKED.md #8."
        )
    return secret


def is_configured() -> bool:
    """For the billing screen, so it can say "not set up yet" plainly."""
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


@dataclass(frozen=True, slots=True)
class Subscription:
    id: str
    customer_id: str
    status: str
    price_cents: int
    current_period_start: int
    current_period_end: int
    cancel_at: int | None


def verify_webhook(
    raw_body: bytes,
    signature_header: str,
    *,
    secret: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Verify a Stripe webhook and return its parsed event.

    Stripe signs `{timestamp}.{raw_body}` with HMAC-SHA256 and sends it as
    `t=...,v1=...`. Three signature schemes now live in this repo — HMAC over
    dots for xAI, Ed25519 over a pipe for Telnyx, and this — and each is a
    separate function for the same reason: one verifier with a scheme
    parameter is how one of them ends up silently unverified.
    """
    import json

    if not isinstance(raw_body, bytes | bytearray):
        raise WebhookVerificationFailed(
            "raw_body must be bytes. Re-serialising the JSON breaks the signature."
        )

    parts = dict(piece.split("=", 1) for piece in signature_header.split(",") if "=" in piece)
    timestamp = parts.get("t")
    provided = parts.get("v1")
    if not timestamp or not provided:
        raise WebhookVerificationFailed("malformed Stripe-Signature header")

    try:
        sent_at = int(timestamp)
    except ValueError as exc:
        raise WebhookVerificationFailed("Stripe-Signature timestamp is not an integer") from exc

    current = time.time() if now is None else now
    if abs(current - sent_at) > WEBHOOK_TOLERANCE_SECONDS:
        raise WebhookVerificationFailed(
            f"webhook is {int(abs(current - sent_at))}s out of tolerance; "
            "an old signature is a replayable one"
        )

    expected = hmac.new(
        (secret or webhook_secret()).encode(),
        f"{timestamp}.".encode() + bytes(raw_body),
        hashlib.sha256,
    ).hexdigest()

    # compare_digest, not ==. A timing side channel leaks the signature.
    if not hmac.compare_digest(expected, provided):
        logger.warning("stripe webhook signature mismatch")
        raise WebhookVerificationFailed("signature did not match")

    return json.loads(raw_body)


class StripeClient:
    """The live client. Refuses without a key, refuses under pytest."""

    def __init__(
        self, *, key: str | None = None, transport: httpx.AsyncBaseTransport | None = None
    ):
        if _under_pytest() and transport is None:
            raise StripeRefusedUnderTest(
                "StripeClient refuses to run under pytest. Bind FakeStripeClient. "
                "A test that reaches live Stripe can create a real subscription."
            )
        self._key = key if key is not None else api_key()
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=DEFAULT_TIMEOUT,
            transport=transport,
            headers={"Authorization": f"Bearer {self._key}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def create_customer(self, *, email: str, business_name: str) -> str:
        response = await self._client.post(
            "/customers", data={"email": email, "name": business_name}
        )
        self._raise_for_status(response, "create customer")
        return str(response.json()["id"])

    async def create_checkout_session(
        self, *, customer_id: str, price_id: str, success_url: str, cancel_url: str
    ) -> str:
        """Stripe's hosted checkout. Returns the URL to send them to.

        Hosted rather than an embedded form on purpose: the card never touches
        our domain, which keeps us out of PCI scope.
        """
        response = await self._client.post(
            "/checkout/sessions",
            data={
                "customer": customer_id,
                "mode": "subscription",
                "line_items[0][price]": price_id,
                "line_items[0][quantity]": 1,
                "success_url": success_url,
                "cancel_url": cancel_url,
            },
        )
        self._raise_for_status(response, "create checkout session")
        return str(response.json()["url"])

    async def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        """Stripe's customer portal: payment method, invoices, cancellation.

        02-PORTAL.md asks for it embedded. Using Stripe's own means changing a
        card, downloading an invoice and cancelling are all handled by somebody
        whose job that is, and none of them is a form we have to get right.
        """
        response = await self._client.post(
            "/billing_portal/sessions",
            data={"customer": customer_id, "return_url": return_url},
        )
        self._raise_for_status(response, "create portal session")
        return str(response.json()["url"])

    async def report_overage(
        self, *, subscription_item_id: str, minutes: int, idempotency_key: str
    ) -> None:
        """Report metered usage.

        The idempotency key is required, not optional. A retried usage report
        without one double-bills a customer, and a customer who was
        double-billed does not come back.
        """
        response = await self._client.post(
            f"/subscription_items/{subscription_item_id}/usage_records",
            data={"quantity": minutes, "action": "set"},
            headers={"Idempotency-Key": idempotency_key},
        )
        self._raise_for_status(response, "report usage")

    @staticmethod
    def _raise_for_status(response: httpx.Response, what: str) -> None:
        if response.status_code >= 400:
            # The body can carry customer details. The status and the operation
            # are enough to act on.
            raise StripeError(f"stripe {what} returned {response.status_code}")


@dataclass
class FakeStripeClient:
    """What tests bind. Records what was asked; charges nothing."""

    customer_id: str = "cus_fake"
    checkout_url: str = "https://checkout.stripe.test/session"
    portal_url: str = "https://billing.stripe.test/session"
    calls: list[tuple[str, dict[str, Any]]] | None = None

    def __post_init__(self) -> None:
        self.calls = []

    async def aclose(self) -> None:
        return None

    def _record(self, name: str, **kwargs: Any) -> None:
        assert self.calls is not None
        self.calls.append((name, kwargs))

    async def create_customer(self, *, email: str, business_name: str) -> str:
        self._record("create_customer", email=email, business_name=business_name)
        return self.customer_id

    async def create_checkout_session(
        self, *, customer_id: str, price_id: str, success_url: str, cancel_url: str
    ) -> str:
        self._record("create_checkout_session", customer_id=customer_id, price_id=price_id)
        return self.checkout_url

    async def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        self._record("create_portal_session", customer_id=customer_id)
        return self.portal_url

    async def report_overage(
        self, *, subscription_item_id: str, minutes: int, idempotency_key: str
    ) -> None:
        self._record(
            "report_overage",
            subscription_item_id=subscription_item_id,
            minutes=minutes,
            idempotency_key=idempotency_key,
        )


Client = StripeClient | FakeStripeClient
