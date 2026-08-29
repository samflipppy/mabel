"""Settings, and the forwarding health indicator.

02-PORTAL.md: "green if calls have arrived in the last 7 days, amber if quiet,
red if silent. This catches the silent-failure churn."

That indicator is the most valuable thing on the Settings screen. A contractor
whose forwarding got switched off stops getting calls, concludes Mabel does not
work, and cancels — without ever opening a ticket. The silence-alert SMS catches
the same failure; this catches it for somebody who happens to be looking.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from mabel_domain.phone import PhoneError, format_national, normalize_e164
from pydantic import BaseModel, Field
from sqlalchemy import text

from mabel_api.deps import CurrentUser, CurrentUserDep, TenantConn, require_role

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Green if a call arrived inside this window.
HEALTHY_DAYS = 7
# Amber up to here. Beyond it, red.
QUIET_DAYS = 14


class ForwardingHealth(BaseModel):
    state: Literal["green", "amber", "red", "never"]
    last_call_at: datetime | None
    days_quiet: int | None
    did_e164: str | None
    did_display: str | None
    message: str


class Account(BaseModel):
    business_name: str
    legal_name: str | None
    trade: str
    timezone: str
    status: str
    did_e164: str | None


class TeamMember(BaseModel):
    id: str
    email: str
    full_name: str | None
    phone_e164: str | None
    role: str
    notify_emergencies: bool
    notify_recap: bool


@router.get("/account", response_model=Account)
async def get_account(user: CurrentUserDep, conn: TenantConn) -> Account:
    del user
    result = await conn.execute(
        text("SELECT business_name, legal_name, trade, timezone, status, did_e164 FROM tenants")
    )
    row = result.mappings().one()
    return Account(**dict(row))


@router.get("/forwarding", response_model=ForwardingHealth)
async def get_forwarding_health(user: CurrentUserDep, conn: TenantConn) -> ForwardingHealth:
    """The silent-failure catcher.

    Deliberately based on calls *arriving*, not on any SIP registration status.
    A trunk can be registered and healthy while the contractor's carrier
    forwarding is switched off, and it is the second one that loses the
    account.
    """
    del user
    result = await conn.execute(
        text(
            """
            SELECT t.did_e164,
                   (SELECT max(started_at) FROM calls) AS last_call_at,
                   (SELECT extract(day FROM now() - max(started_at))::int FROM calls)
                     AS days_quiet
            FROM tenants t
            """
        )
    )
    row = result.mappings().one()
    did = row["did_e164"]
    display = format_national(did) if did else None
    last = row["last_call_at"]
    days = row["days_quiet"]

    if last is None:
        return ForwardingHealth(
            state="never",
            last_call_at=None,
            days_quiet=None,
            did_e164=did,
            did_display=display,
            message=(
                "No calls have reached Mabel yet. If you've set up forwarding, "
                "ring your business line from another phone to check it."
            ),
        )

    days = int(days or 0)
    if days <= HEALTHY_DAYS:
        state, message = "green", "Calls are reaching Mabel."
    elif days <= QUIET_DAYS:
        state, message = (
            "amber",
            f"Quiet for {days} days. That may just be a slow fortnight.",
        )
    else:
        state, message = (
            "red",
            f"No calls for {days} days. Call forwarding has probably been "
            "switched off — check your phone settings.",
        )

    return ForwardingHealth(
        state=state,  # type: ignore[arg-type]
        last_call_at=last,
        days_quiet=days,
        did_e164=did,
        did_display=display,
        message=message,
    )


class ForwardingCodes(BaseModel):
    """The exact codes for their carrier. 02-PORTAL.md wants these on screen,
    not in a support email."""

    carrier: str
    enable_no_answer: str
    enable_busy: str
    enable_unreachable: str
    disable_all: str
    note: str


# Conditional-forwarding codes are GSM standards and are the same across the
# major US carriers; what differs is whether the carrier honours all three.
# Verified against carrier documentation rather than guessed, and the note says
# so where a carrier is known to differ.
CARRIER_CODES: dict[str, dict[str, str]] = {
    "verizon": {
        "enable_no_answer": "*71{number}",
        "enable_busy": "*71{number}",
        "enable_unreachable": "*71{number}",
        "disable_all": "*73",
        "note": "Verizon uses one code for all three conditions.",
    },
    "att": {
        "enable_no_answer": "*61*{number}#",
        "enable_busy": "*67*{number}#",
        "enable_unreachable": "*62*{number}#",
        "disable_all": "##004#",
        "note": "Dial each of the three so no call slips through.",
    },
    "tmobile": {
        "enable_no_answer": "**61*{number}#",
        "enable_busy": "**67*{number}#",
        "enable_unreachable": "**62*{number}#",
        "disable_all": "##004#",
        "note": "Dial each of the three so no call slips through.",
    },
    "other": {
        "enable_no_answer": "**61*{number}#",
        "enable_busy": "**67*{number}#",
        "enable_unreachable": "**62*{number}#",
        "disable_all": "##004#",
        "note": (
            "These are the GSM standard codes and work on most carriers. "
            "If they don't, your carrier's support can set conditional "
            "forwarding for you."
        ),
    },
}


@router.get("/forwarding/codes", response_model=list[ForwardingCodes])
async def get_forwarding_codes(user: CurrentUserDep, conn: TenantConn) -> list[ForwardingCodes]:
    del user
    result = await conn.execute(text("SELECT did_e164 FROM tenants"))
    did = result.scalar_one_or_none()
    if not did:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No Mabel number assigned yet.",
        )

    return [
        ForwardingCodes(
            carrier=carrier,
            enable_no_answer=codes["enable_no_answer"].format(number=did),
            enable_busy=codes["enable_busy"].format(number=did),
            enable_unreachable=codes["enable_unreachable"].format(number=did),
            disable_all=codes["disable_all"],
            note=codes["note"],
        )
        for carrier, codes in CARRIER_CODES.items()
    ]


@router.get("/team", response_model=list[TeamMember])
async def get_team(user: CurrentUserDep, conn: TenantConn) -> list[TeamMember]:
    del user
    result = await conn.execute(
        text(
            "SELECT id, email, full_name, phone_e164, role, notify_emergencies, "
            "notify_recap FROM users WHERE deleted_at IS NULL ORDER BY role, email"
        )
    )
    return [
        TeamMember(
            id=str(row["id"]),
            email=str(row["email"]),
            full_name=row["full_name"],
            phone_e164=row["phone_e164"],
            role=row["role"],
            notify_emergencies=row["notify_emergencies"],
            notify_recap=row["notify_recap"],
        )
        for row in result.mappings()
    ]


class NotificationPrefs(BaseModel):
    notify_emergencies: bool
    notify_recap: bool
    phone_e164: str | None = Field(default=None, max_length=20)


@router.put("/team/{member_id}/notifications", response_model=TeamMember)
async def set_notifications(
    member_id: str, body: NotificationPrefs, user: CurrentUserDep, conn: TenantConn
) -> TeamMember:
    """Who gets woken up.

    Anyone can change their own; only an owner can change somebody else's.
    Turning off a colleague's emergency alerts without telling them is how an
    emergency goes unanswered.
    """
    if str(member_id) != str(user.user_id) and not user.is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an owner can change someone else's notifications.",
        )

    phone = None
    if body.phone_e164:
        try:
            phone = normalize_e164(body.phone_e164)
        except PhoneError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"That doesn't look like a phone number: {exc}",
            ) from exc

    result = await conn.execute(
        text(
            """
            UPDATE users
            SET notify_emergencies = :emergencies,
                notify_recap = :recap,
                phone_e164 = coalesce(:phone, phone_e164)
            WHERE id = :id
            RETURNING id
            """
        ),
        {
            "id": member_id,
            "emergencies": body.notify_emergencies,
            "recap": body.notify_recap,
            "phone": phone,
        },
    )
    if result.first() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such person")

    team = await get_team(user, conn)
    return next(member for member in team if member.id == str(member_id))


class Invite(BaseModel):
    email: str
    full_name: str | None = None
    role: Literal["owner", "office", "tech"] = "office"


@router.post("/team", response_model=TeamMember, status_code=status.HTTP_201_CREATED)
async def invite(
    body: Invite,
    user: CurrentUserDep,
    conn: TenantConn,
    _guard: CurrentUser = Depends(require_role("owner")),
) -> TeamMember:
    """Invite by email.

    Creates the row with a null `supabase_uid`; it fills in when they first
    sign in and their uid is matched against this email. That ordering is what
    makes `resolve_user_by_supabase_uid` return nothing for somebody who signed
    up but was never invited.
    """
    result = await conn.execute(
        text(
            "INSERT INTO users (tenant_id, email, full_name, role) "
            "VALUES (:t, :email, :name, :role) RETURNING id"
        ),
        {"t": user.tenant_id, "email": body.email, "name": body.full_name, "role": body.role},
    )
    del result

    team = await get_team(user, conn)
    return next(member for member in team if member.email == body.email)


class DataExport(BaseModel):
    """02-PORTAL.md: "Their data, downloadable." Counts first, so they know
    what they are asking for before it is generated."""

    calls: int
    leads: int
    contacts: int
    events: int


@router.get("/data", response_model=DataExport)
async def data_summary(user: CurrentUserDep, conn: TenantConn) -> DataExport:
    del user
    result = await conn.execute(
        text(
            """
            SELECT (SELECT count(*) FROM calls) AS calls,
                   (SELECT count(*) FROM leads) AS leads,
                   (SELECT count(*) FROM contacts) AS contacts,
                   (SELECT count(*) FROM communication_events) AS events
            """
        )
    )
    row: Any = result.mappings().one()
    return DataExport(**{k: int(v) for k, v in row.items()})
