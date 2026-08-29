"""The plans, in integer cents.

Invariant 5: money is integer cents, and Stripe speaks cents too — so there is
no conversion anywhere in this path and no opportunity for a rounding
disagreement between what we charge and what we say we charged.

The three plans come from 01-SCHEMA.sql's CHECK constraint. The prices come
from 00-STACK.md's margin table, where $299 is the number the whole cost model
is built around.

**Nothing here reads a Stripe price object to find out what something costs.**
The plan is defined here and the Stripe price id is a pointer to it. If the two
disagree, that is a deployment mistake worth failing on rather than quietly
charging whatever Stripe happens to say.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum

from mabel_domain.money import Money


class PlanKey(StrEnum):
    MABEL = "mabel"
    FULLTIME = "fulltime"
    PLUS = "plus"


@dataclass(frozen=True, slots=True)
class Plan:
    key: PlanKey
    name: str
    price_cents: int
    included_minutes: int
    overage_cents_per_min: int
    blurb: str

    @property
    def price(self) -> Money:
        return Money(self.price_cents)

    def overage_cents(self, minutes_used: float) -> int:
        """What the overage costs, in integer cents.

        Rounds the overage minutes up. A customer who used 90.2 minutes over
        has used 91 minutes we paid for, and rounding down means eating the
        difference on every invoice.
        """
        import math

        over = max(0.0, minutes_used - self.included_minutes)
        if over == 0:
            return 0
        return math.ceil(over) * self.overage_cents_per_min


# 00-STACK.md's cost model: ~$10 variable per customer per month at ~90 voice
# minutes. The included allowance is set above that so an ordinary month never
# produces an overage line — a surprise overage bill is how answering services
# lose customers, and the cheapest way to avoid one is to not generate it.
PLANS: dict[PlanKey, Plan] = {
    PlanKey.MABEL: Plan(
        key=PlanKey.MABEL,
        name="Mabel",
        price_cents=29_900,
        included_minutes=150,
        overage_cents_per_min=50,
        blurb="She answers after hours. Everything in the portal.",
    ),
    PlanKey.FULLTIME: Plan(
        key=PlanKey.FULLTIME,
        name="Mabel Full-time",
        price_cents=49_900,
        included_minutes=400,
        overage_cents_per_min=45,
        blurb="She also picks up when your line is busy during the day.",
    ),
    PlanKey.PLUS: Plan(
        key=PlanKey.PLUS,
        name="Mabel Plus",
        price_cents=79_900,
        included_minutes=900,
        overage_cents_per_min=40,
        blurb="Multiple locations, multiple numbers, one inbox.",
    ),
}


def plan(key: PlanKey | str) -> Plan:
    try:
        return PLANS[PlanKey(key)]
    except (ValueError, KeyError) as exc:
        raise ValueError(f"unknown plan {key!r}") from exc


def stripe_price_id(key: PlanKey | str) -> str | None:
    """The Stripe price for a plan, from the environment.

    Returns None when unset rather than raising, so the billing screen can say
    "not configured yet" instead of erroring. See docs/BLOCKED.md #8.

    Deliberately not hardcoded: price ids differ between Stripe's test and live
    modes, and a hardcoded one is how a test-mode id ends up in production.
    """
    return os.environ.get(f"STRIPE_PRICE_{PlanKey(key).value.upper()}")


@dataclass(frozen=True, slots=True)
class Invoice:
    """What the next bill will be. Every figure is integer cents."""

    plan_cents: int
    overage_cents: int
    minutes_used: float
    minutes_included: int

    @property
    def total_cents(self) -> int:
        return self.plan_cents + self.overage_cents

    @property
    def total(self) -> Money:
        return Money(self.total_cents)

    @property
    def is_over(self) -> bool:
        return self.overage_cents > 0


def estimate_invoice(key: PlanKey | str, minutes_used: float) -> Invoice:
    """What they will be charged, computed the same way the invoice is.

    The portal shows this before the invoice arrives, which is the whole point:
    02-PORTAL.md wants usage "transparent, because surprise overage bills are
    how answering services lose customers."
    """
    chosen = plan(key)
    return Invoice(
        plan_cents=chosen.price_cents,
        overage_cents=chosen.overage_cents(minutes_used),
        minutes_used=round(minutes_used, 2),
        minutes_included=chosen.included_minutes,
    )
