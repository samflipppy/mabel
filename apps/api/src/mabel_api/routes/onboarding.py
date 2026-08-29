"""The six-step onboarding wizard. Target under fifteen minutes.

02-PORTAL.md: "That last step is the whole onboarding. Everything else is
prefilled." Steps one to five are confirmations of things Sam already entered
during the sale; step six is the one that decides whether the product works at
all, because a contractor who does not forward his phone gets nothing.

So the state machine below is deliberately permissive about the first five —
they can be revisited, skipped forward, and come back — and strict about the
last: it does not complete on a button press, it completes when a call actually
arrives.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, status
from mabel_domain.phone import PhoneError, format_national, normalize_e164
from pydantic import BaseModel, Field
from sqlalchemy import text

from mabel_api.deps import CurrentUserDep, TenantConn

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

STEPS = (
    "business",
    "hours",
    "services",
    "emergencies",
    "notify",
    "forward",
)

# How recently a call has to have arrived for step six to count as done.
VERIFICATION_WINDOW_MINUTES = 30


class StepState(BaseModel):
    key: str
    label: str
    complete: bool
    detail: str | None = None


class OnboardingState(BaseModel):
    steps: list[StepState]
    current: str
    complete: bool
    did_display: str | None


@router.get("/state", response_model=OnboardingState)
async def get_state(user: CurrentUserDep, conn: TenantConn) -> OnboardingState:
    """What is done and what is next.

    Derived from the data rather than from a stored step counter. A wizard that
    tracks its own position gets out of step with reality the moment somebody
    edits something on the Mabel screen instead.
    """
    del user
    tenant = await conn.execute(
        text("SELECT business_name, trade, timezone, did_e164 FROM tenants")
    )
    row = tenant.mappings().one()

    config = await conn.execute(
        text(
            "SELECT greeting, business_hours, services, service_area_zips, "
            "emergency_overrides FROM agent_configs WHERE is_live"
        )
    )
    live = config.mappings().one_or_none()

    notified = await conn.execute(
        text(
            "SELECT count(*) FROM users "
            "WHERE notify_emergencies AND phone_e164 IS NOT NULL AND deleted_at IS NULL"
        )
    )

    arrived = await conn.execute(text("SELECT count(*) FROM calls"))
    calls_arrived = int(arrived.scalar_one())

    steps = [
        StepState(
            key="business",
            label="Your business",
            complete=bool(row["business_name"] and row["trade"]),
            detail=row["business_name"],
        ),
        StepState(
            key="hours",
            label="When you're closed",
            complete=bool(live and live["business_hours"]),
        ),
        StepState(
            key="services",
            label="What you do, and where",
            complete=bool(live and (live["services"] or live["service_area_zips"])),
        ),
        StepState(
            key="emergencies",
            label="What's worth waking you",
            # The trade ruleset is preselected, so this is complete as soon as
            # a config exists. Adjusting it is optional by design.
            complete=bool(live),
        ),
        StepState(
            key="notify",
            label="Who to text",
            complete=int(notified.scalar_one()) > 0,
        ),
        StepState(
            key="forward",
            label="Forward your phone",
            # The only step that cannot be self-certified.
            complete=calls_arrived > 0,
            detail=("A call has reached Mabel." if calls_arrived else "No calls yet."),
        ),
    ]

    current = next((step.key for step in steps if not step.complete), "forward")
    return OnboardingState(
        steps=steps,
        current=current,
        complete=all(step.complete for step in steps),
        did_display=format_national(row["did_e164"]) if row["did_e164"] else None,
    )


class BusinessStep(BaseModel):
    business_name: str = Field(min_length=1, max_length=200)
    trade: str
    timezone: str


@router.put("/business", response_model=OnboardingState)
async def save_business(
    body: BusinessStep, user: CurrentUserDep, conn: TenantConn
) -> OnboardingState:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(body.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{body.timezone!r} isn't a timezone we recognise.",
        ) from exc

    await conn.execute(
        text("UPDATE tenants SET business_name = :name, trade = :trade, timezone = :tz"),
        {"name": body.business_name, "trade": body.trade, "tz": body.timezone},
    )
    return await get_state(user, conn)


class NotifyStep(BaseModel):
    phone_e164: str
    full_name: str | None = None


@router.put("/notify", response_model=OnboardingState)
async def save_notify(body: NotifyStep, user: CurrentUserDep, conn: TenantConn) -> OnboardingState:
    """The owner's cell, "confirmed by a test text".

    The text is queued rather than sent inline, so it goes through the same
    retrying path as every other message and a Telnyx outage does not look like
    a broken wizard.
    """
    try:
        phone = normalize_e164(body.phone_e164)
    except PhoneError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"That doesn't look like a mobile number: {exc}",
        ) from exc

    await conn.execute(
        text(
            "UPDATE users SET phone_e164 = :phone, notify_emergencies = true, "
            "notify_recap = true, full_name = coalesce(cast(:name as text), full_name) "
            "WHERE id = :id"
        ),
        {"phone": phone, "name": body.full_name, "id": user.user_id},
    )

    from mabel_db.queries.notifications import enqueue

    await enqueue(
        conn,
        tenant_id=user.tenant_id,
        kind="system",
        channel="sms",
        to_address=phone,
        body=("This is Mabel. You'll get a text here when something's urgent, and a recap at 7am."),
        user_id=user.user_id,
    )
    return await get_state(user, conn)


class VerificationResult(BaseModel):
    verified: bool
    message: str
    call_at: datetime | None


@router.get("/verify-forwarding", response_model=VerificationResult)
async def verify_forwarding(user: CurrentUserDep, conn: TenantConn) -> VerificationResult:
    """The live verification: "Call your business line now. We'll tell you when
    it reaches Mabel."

    Polled by the wizard. Looks for a call in the last half hour rather than
    any call ever, so somebody re-running onboarding months later has to
    actually re-test rather than being told it worked in June.
    """
    del user
    since = datetime.now(UTC) - timedelta(minutes=VERIFICATION_WINDOW_MINUTES)
    result = await conn.execute(
        text("SELECT max(started_at) FROM calls WHERE started_at > :since"),
        {"since": since},
    )
    arrived = result.scalar_one_or_none()

    if arrived is None:
        return VerificationResult(
            verified=False,
            message=(
                "Nothing yet. Ring your business line from another phone and let it go to Mabel."
            ),
            call_at=None,
        )

    return VerificationResult(
        verified=True,
        message="That's it — a call reached Mabel. Forwarding is working.",
        call_at=arrived,
    )


class CompleteRequest(BaseModel):
    """Finishing without a verified call, deliberately.

    Some contractors set forwarding up at the carrier's office and cannot test
    it there and then. Refusing to let them finish would be worse than letting
    them, so this is allowed — and the Settings screen's forwarding indicator
    is red until a call actually arrives, which is the durable version of the
    same warning.
    """

    acknowledged_untested: bool = False


@router.post("/complete", response_model=OnboardingState)
async def complete(
    body: CompleteRequest, user: CurrentUserDep, conn: TenantConn
) -> OnboardingState:
    state = await get_state(user, conn)

    forward = next(step for step in state.steps if step.key == "forward")
    if not forward.complete and not body.acknowledged_untested:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No call has reached Mabel yet. Test your forwarding, or tick "
                "the box to finish anyway and test later."
            ),
        )

    await conn.execute(
        text(
            "INSERT INTO audit_log (tenant_id, actor_id, actor_type, action, entity, after) "
            "VALUES (:t, :actor, 'user', 'onboarding_completed', 'tenant', "
            "cast(:after as jsonb))"
        ),
        {
            "t": user.tenant_id,
            "actor": user.user_id,
            "after": _json({"forwarding_verified": forward.complete}),
        },
    )
    return state


def _json(value: Any) -> str:
    import json

    return json.dumps(value)
