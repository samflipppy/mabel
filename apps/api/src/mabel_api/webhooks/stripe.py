"""Stripe webhooks. Subscription lifecycle.

Same three rules as every other webhook here (invariant 8): verify against the
raw body, reject an out-of-tolerance timestamp, and be idempotent on the event
id.

**Tenant is resolved from the Stripe customer id, not from the event body's
metadata.** Metadata is settable from the Stripe dashboard by anyone with
access to it; the customer id is the thing we stored when we created the
customer. Same principle as resolving a call from the dialed number.

**Nothing here cancels service.** A `customer.subscription.deleted` sets the
tenant to `churned` and stops billing. It does not delete data, release the
DID, or take the agent offline — every one of those is irreversible and needs
a human.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from mabel_billing.plans import PlanKey, plan
from mabel_billing.stripe_client import (
    StripeUnavailable,
    WebhookVerificationFailed,
    verify_webhook,
)
from mabel_db.tenant import admin_scope, tenant_scope
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter()

# The events we act on. Anything else is acknowledged and ignored — Stripe
# sends a great many, and reacting to one we did not plan for is worse than
# ignoring it.
HANDLED = {
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.payment_failed",
    "invoice.payment_succeeded",
}


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request) -> JSONResponse:
    raw = await request.body()
    signature = request.headers.get("stripe-signature", "")

    try:
        event = verify_webhook(raw, signature)
    except StripeUnavailable:
        # No secret configured. Refuse rather than process — an unverified
        # billing event could set a tenant to active without payment.
        logger.warning("stripe webhook arrived but no signing secret is configured")
        return JSONResponse(status_code=503, content={"error": "not configured"})
    except WebhookVerificationFailed as exc:
        logger.warning("rejected a stripe webhook: %s", exc)
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    event_id = str(event.get("id", ""))
    event_type = str(event.get("type", ""))

    if event_id and not await _first_time(event_id):
        return JSONResponse(content={"status": "duplicate"})

    if event_type not in HANDLED:
        return JSONResponse(content={"status": "ignored", "type": event_type})

    obj = (event.get("data") or {}).get("object") or {}
    customer_id = obj.get("customer")
    if not customer_id:
        return JSONResponse(content={"status": "ignored", "reason": "no customer"})

    tenant_id = await _tenant_for_customer(str(customer_id))
    if tenant_id is None:
        # A customer we do not know. Acknowledged so Stripe stops retrying,
        # logged because it should not happen.
        logger.warning("stripe event for an unknown customer")
        return JSONResponse(content={"status": "ignored", "reason": "unknown customer"})

    async with tenant_scope(tenant_id) as conn:
        await _apply(conn, tenant_id, event_type, obj)

    return JSONResponse(content={"status": "ok", "type": event_type})


async def _apply(conn: Any, tenant_id: UUID, event_type: str, obj: dict[str, Any]) -> None:
    if event_type in {
        "checkout.session.completed",
        "customer.subscription.created",
        "customer.subscription.updated",
    }:
        await _upsert_subscription(conn, tenant_id, obj)
        await conn.execute(
            text("UPDATE tenants SET status = 'active' WHERE status IN ('trial', 'past_due')")
        )

    elif event_type == "customer.subscription.deleted":
        await conn.execute(
            text("UPDATE subscriptions SET status = 'canceled' WHERE stripe_subscription_id = :id"),
            {"id": obj.get("id")},
        )
        # Status only. No data deleted, no number released, no agent taken
        # offline — all irreversible, all needing a human.
        await conn.execute(text("UPDATE tenants SET status = 'churned'"))
        logger.info("tenant %s churned; data and number left untouched", tenant_id)

    elif event_type == "invoice.payment_failed":
        # past_due, not paused. A contractor whose card expired should not lose
        # his after-hours answering the same night — that is a support problem
        # dressed up as a billing one.
        await conn.execute(text("UPDATE tenants SET status = 'past_due'"))

    elif event_type == "invoice.payment_succeeded":
        await conn.execute(text("UPDATE tenants SET status = 'active' WHERE status = 'past_due'"))


async def _upsert_subscription(conn: Any, tenant_id: UUID, obj: dict[str, Any]) -> None:
    """Write what Stripe says, priced from our own plan table.

    The amount comes from `plans.py`, not from the Stripe object. If the two
    disagree that is a deployment mistake worth noticing rather than silently
    recording whatever Stripe reported.
    """
    subscription_id = obj.get("id") or obj.get("subscription")
    if not subscription_id:
        return

    plan_key = _plan_from(obj)
    if plan_key is None:
        logger.warning("stripe subscription with no recognisable plan")
        return

    chosen = plan(plan_key)
    await conn.execute(
        text(
            """
            INSERT INTO subscriptions
              (tenant_id, stripe_subscription_id, plan, price_cents, included_minutes,
               overage_cents_per_min, status, current_period_start, current_period_end)
            VALUES
              (:tenant_id, :sub, :plan, :price, :included, :overage, :status,
               to_timestamp(:start), to_timestamp(:end))
            ON CONFLICT (stripe_subscription_id) DO UPDATE SET
              plan = excluded.plan,
              price_cents = excluded.price_cents,
              included_minutes = excluded.included_minutes,
              overage_cents_per_min = excluded.overage_cents_per_min,
              status = excluded.status,
              current_period_start = excluded.current_period_start,
              current_period_end = excluded.current_period_end
            """
        ),
        {
            "tenant_id": tenant_id,
            "sub": str(subscription_id),
            "plan": str(chosen.key),
            "price": chosen.price_cents,
            "included": chosen.included_minutes,
            "overage": chosen.overage_cents_per_min,
            "status": obj.get("status", "active"),
            "start": obj.get("current_period_start") or 0,
            "end": obj.get("current_period_end") or 0,
        },
    )


def _plan_from(obj: dict[str, Any]) -> PlanKey | None:
    """Which of our plans this subscription is.

    Matched on the Stripe price id we configured, so a price created by hand in
    the dashboard does not silently map onto a plan it is not.
    """
    from mabel_billing.plans import stripe_price_id

    items = ((obj.get("items") or {}).get("data")) or []
    price_ids = {(item.get("price") or {}).get("id") for item in items if isinstance(item, dict)}
    price_ids.discard(None)

    for key in PlanKey:
        configured = stripe_price_id(key)
        if configured and configured in price_ids:
            return key
    return None


async def _tenant_for_customer(customer_id: str) -> UUID | None:
    """Resolve the tenant from the Stripe customer id.

    The fourth instance of the pattern from migrations 0003 to 0005: something
    arrives from outside carrying an external identifier, and the table that
    knows which tenant it belongs to is RLS-protected with no tenant context
    available yet. Same answer as the other three — a narrow SECURITY DEFINER
    function, migration 0006.
    """
    async with admin_scope(reason="resolve a stripe customer", engine=None) as conn:
        result = await conn.execute(
            text("SELECT tenant_id FROM resolve_tenant_by_stripe_customer(:cid)"),
            {"cid": customer_id},
        )
        return result.scalar_one_or_none()


async def _first_time(event_id: str) -> bool:
    """Idempotency, with a caveat worth knowing.

    `webhook_receipts` is pruned every ten minutes by the cron in 0002, which
    matches xAI's and Telnyx's retry behaviour. **Stripe retries for up to
    three days**, so a very late retry will not be recognised as a duplicate
    here.

    That is survivable because every handler below is idempotent by
    construction — the subscription insert upserts, and the status writes are
    assignments rather than increments — so reprocessing a three-day-old event
    reaches the same state. It would not be survivable for anything that
    incremented a counter or sent a message, and nothing here does.
    """
    async with admin_scope(reason="stripe webhook idempotency", engine=None) as conn:
        result = await conn.execute(
            text(
                "INSERT INTO webhook_receipts (webhook_id, source) VALUES (:id, 'stripe') "
                "ON CONFLICT (webhook_id) DO NOTHING RETURNING webhook_id"
            ),
            {"id": event_id},
        )
        return result.first() is not None
