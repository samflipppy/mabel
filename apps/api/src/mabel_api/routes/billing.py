"""The Billing screen.

02-PORTAL.md: "plan, next invoice, payment method, invoice history, upgrade or
downgrade, cancel. Stripe customer portal embedded."

Everything that touches a card is a redirect to Stripe's own hosted pages.
Changing a payment method, downloading an invoice and cancelling are all
handled by somebody whose job that is, and none of them is a form we have to
get right or a PCI obligation we have to carry.

The one thing computed here is the estimated next invoice, because 02-PORTAL.md
wants usage transparent before the bill arrives rather than after.
"""

from __future__ import annotations

import logging
import os
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from mabel_billing.plans import PLANS, PlanKey, estimate_invoice, stripe_price_id
from mabel_billing.stripe_client import (
    FakeStripeClient,
    StripeClient,
    StripeError,
    StripeUnavailable,
    is_configured,
)
from pydantic import BaseModel
from sqlalchemy import text

from mabel_api.deps import CurrentUser, CurrentUserDep, TenantConn, require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/billing", tags=["billing"])


class PlanOption(BaseModel):
    key: str
    name: str
    price_cents: int
    included_minutes: int
    overage_cents_per_min: int
    blurb: str
    is_current: bool


class BillingState(BaseModel):
    configured: bool
    plan_key: str | None
    subscription_status: str | None
    current_period_end: str | None
    # Every figure is integer cents. The browser formats.
    plan_cents: int | None
    estimated_overage_cents: int
    estimated_total_cents: int | None
    minutes_used: float
    minutes_included: int | None
    plans: list[PlanOption]
    message: str | None


def build_client():
    """The live client, or a fake that charges nothing.

    Unlike the SMS sender — where a fake would record messages as sent that
    were never sent — a fake here cannot mislead anybody: `configured` is False
    in the response and the screen says billing is not set up. The fake exists
    so the rest of the screen still renders.
    """
    try:
        return StripeClient()
    except StripeUnavailable:
        return FakeStripeClient()


@router.get("", response_model=BillingState)
async def get_billing(user: CurrentUserDep, conn: TenantConn) -> BillingState:
    del user
    subscription = await conn.execute(
        text(
            "SELECT plan, status, price_cents, included_minutes, current_period_end "
            "FROM subscriptions ORDER BY created_at DESC LIMIT 1"
        )
    )
    row = subscription.mappings().one_or_none()

    used = await conn.execute(
        text(
            """
            SELECT coalesce(sum(voice_minutes), 0) AS minutes
            FROM usage_daily
            WHERE day >= date_trunc('month', current_date)::date
            """
        )
    )
    minutes = float(used.scalar_one() or 0)

    plans = [
        PlanOption(
            key=str(option.key),
            name=option.name,
            price_cents=option.price_cents,
            included_minutes=option.included_minutes,
            overage_cents_per_min=option.overage_cents_per_min,
            blurb=option.blurb,
            is_current=bool(row and row["plan"] == str(option.key)),
        )
        for option in PLANS.values()
    ]

    if row is None:
        return BillingState(
            configured=is_configured(),
            plan_key=None,
            subscription_status=None,
            current_period_end=None,
            plan_cents=None,
            estimated_overage_cents=0,
            estimated_total_cents=None,
            minutes_used=round(minutes, 2),
            minutes_included=None,
            plans=plans,
            message=(
                "No subscription yet."
                if is_configured()
                else "Billing isn't set up yet. Nothing to pay, nothing to do."
            ),
        )

    invoice = estimate_invoice(row["plan"], minutes)
    return BillingState(
        configured=is_configured(),
        plan_key=row["plan"],
        subscription_status=row["status"],
        current_period_end=(
            row["current_period_end"].isoformat() if row["current_period_end"] else None
        ),
        plan_cents=int(row["price_cents"]),
        estimated_overage_cents=invoice.overage_cents,
        estimated_total_cents=invoice.total_cents,
        minutes_used=invoice.minutes_used,
        minutes_included=int(row["included_minutes"]),
        plans=plans,
        message=(
            "You're over your included minutes this month. The overage is on your next invoice."
            if invoice.is_over
            else None
        ),
    )


class CheckoutRequest(BaseModel):
    plan: str


class Redirect(BaseModel):
    url: str


@router.post("/checkout", response_model=Redirect)
async def start_checkout(
    body: CheckoutRequest,
    user: CurrentUserDep,
    conn: TenantConn,
    _guard: CurrentUser = Depends(require_role("owner")),
) -> Redirect:
    """Subscribe, upgrade, or downgrade. Redirects to Stripe's hosted page."""
    try:
        key = PlanKey(body.plan)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No plan called {body.plan!r}.",
        ) from exc

    price_id = stripe_price_id(key)
    if not price_id or not is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing isn't connected yet. Nothing you can do here for now.",
        )

    tenant = await conn.execute(text("SELECT business_name, stripe_customer_id FROM tenants"))
    row = tenant.mappings().one()
    customer_id = row["stripe_customer_id"]

    client = build_client()
    try:
        if not customer_id:
            customer_id = await client.create_customer(
                email=user.email, business_name=row["business_name"]
            )
            await conn.execute(
                text("UPDATE tenants SET stripe_customer_id = :cid"), {"cid": customer_id}
            )

        base = os.environ.get("PORTAL_BASE_URL", "https://app.hiremabel.com")
        url = await client.create_checkout_session(
            customer_id=customer_id,
            price_id=price_id,
            success_url=f"{base}/settings?billing=done",
            cancel_url=f"{base}/settings",
        )
    except StripeError as exc:
        logger.warning("stripe checkout failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Couldn't reach Stripe. Try again in a minute.",
        ) from exc
    finally:
        await client.aclose()

    return Redirect(url=url)


@router.post("/portal", response_model=Redirect)
async def open_portal(
    user: CurrentUserDep,
    conn: TenantConn,
    _guard: CurrentUser = Depends(require_role("owner")),
) -> Redirect:
    """Stripe's customer portal: card, invoices, cancellation.

    Cancellation lives there rather than here on purpose. It is irreversible
    from our side, it is Stripe's flow to get right, and a cancel button we
    wrote is a cancel button we have to be certain about.
    """
    del user
    tenant = await conn.execute(text("SELECT stripe_customer_id FROM tenants"))
    customer_id = tenant.scalar_one_or_none()
    if not customer_id or not is_configured():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No billing account yet.",
        )

    client = build_client()
    try:
        base = os.environ.get("PORTAL_BASE_URL", "https://app.hiremabel.com")
        url = await client.create_portal_session(
            customer_id=customer_id, return_url=f"{base}/settings"
        )
    except StripeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Couldn't reach Stripe."
        ) from exc
    finally:
        await client.aclose()

    return Redirect(url=url)


class UsageSummary(BaseModel):
    month: date
    minutes: float
    calls: int
    sms: int
    # Our cost, not theirs. Internal, and never presented as a charge.
    cost_cents: int


@router.get("/usage-history", response_model=list[UsageSummary])
async def usage_history(user: CurrentUserDep, conn: TenantConn) -> list[UsageSummary]:
    del user
    result = await conn.execute(
        text(
            """
            SELECT date_trunc('month', day)::date AS month,
                   sum(voice_minutes) AS minutes,
                   sum(calls_answered) AS calls,
                   sum(sms_sent) AS sms,
                   sum(cost_cents) AS cost_cents
            FROM usage_daily
            WHERE day > current_date - interval '12 months'
            GROUP BY 1
            ORDER BY 1 DESC
            """
        )
    )
    return [
        UsageSummary(
            month=row["month"],
            minutes=round(float(row["minutes"] or 0), 2),
            calls=int(row["calls"] or 0),
            sms=int(row["sms"] or 0),
            cost_cents=int(row["cost_cents"] or 0),
        )
        for row in result.mappings()
    ]
