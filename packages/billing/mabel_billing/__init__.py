"""Billing. Integer cents throughout, and no card ever touches this repo.

Payment methods are collected by Stripe's own hosted checkout and customer
portal, which is what keeps Mabel out of PCI scope entirely.
"""

from __future__ import annotations

from mabel_billing.plans import (
    PLANS,
    Invoice,
    Plan,
    PlanKey,
    estimate_invoice,
    plan,
    stripe_price_id,
)
from mabel_billing.report_pdf import ReportFigures, build_lines, render_pdf, storage_path
from mabel_billing.stripe_client import (
    Client,
    FakeStripeClient,
    StripeClient,
    StripeError,
    StripeRefusedUnderTest,
    StripeUnavailable,
    Subscription,
    WebhookVerificationFailed,
    api_key,
    is_configured,
    verify_webhook,
    webhook_secret,
)

__all__ = [
    "PLANS",
    "Client",
    "FakeStripeClient",
    "Invoice",
    "Plan",
    "PlanKey",
    "ReportFigures",
    "StripeClient",
    "StripeError",
    "StripeRefusedUnderTest",
    "StripeUnavailable",
    "Subscription",
    "WebhookVerificationFailed",
    "api_key",
    "build_lines",
    "estimate_invoice",
    "is_configured",
    "plan",
    "render_pdf",
    "storage_path",
    "stripe_price_id",
    "verify_webhook",
    "webhook_secret",
]
