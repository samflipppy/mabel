"""The Leads board, and the one field the whole product reports on.

02-PORTAL.md: the value field is labelled *"What's this job worth?"*, is
owner-entered, "drives every report, so make it prominent and easy."

It arrives from the browser as a string a human typed and goes through
`parse_owner_amount`, the same deterministic parser the SMS grammar uses. Two
entry points, one parser, one set of rules about what is and is not a number.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from mabel_domain.money import Money, MoneyError, parse_owner_amount
from pydantic import BaseModel
from sqlalchemy import text

from mabel_api.deps import CurrentUserDep, TenantConn

router = APIRouter(prefix="/api/leads", tags=["leads"])

# The board's columns, in order. Won and Lost are terminal.
STAGES = (
    "new",
    "contacted",
    "estimate_scheduled",
    "estimate_sent",
    "won",
    "lost",
)

UNTOUCHED_HOURS = 24


class Lead(BaseModel):
    id: str
    caller_name: str | None
    service_address: str | None
    callback_e164: str | None
    job_type: str | None
    description: str | None
    urgency: str
    source: str | None
    status: str
    # Cents. Formatted in the browser so nothing is rounded twice.
    value_cents: int | None
    currency: str
    lost_reason: str | None
    created_at: datetime
    first_touched_at: datetime | None
    won_at: datetime | None
    days_in_stage: int
    # The red dot: untouched for over 24 hours.
    is_stale: bool
    contact_id: str | None
    call_id: str | None


class Board(BaseModel):
    stages: dict[str, list[Lead]]
    counts: dict[str, int]
    # Sum of value_cents for won leads on this board. Cents.
    won_value_cents: int


@router.get("/board", response_model=Board)
async def get_board(user: CurrentUserDep, conn: TenantConn) -> Board:
    del user
    result = await conn.execute(
        text(
            """
            SELECT id, caller_name, service_address, callback_e164, job_type,
                   description, urgency, source, status, value_cents, currency,
                   lost_reason, created_at, first_touched_at, won_at,
                   contact_id, call_id,
                   extract(day FROM now() - coalesce(won_at, created_at))::int AS days,
                   (first_touched_at IS NULL AND status = 'new'
                    AND created_at < now() - make_interval(hours => :stale)) AS is_stale
            FROM leads
            WHERE status <> 'spam'
            ORDER BY created_at DESC
            """
        ),
        {"stale": UNTOUCHED_HOURS},
    )

    stages: dict[str, list[Lead]] = {stage: [] for stage in STAGES}
    won_total = 0
    for row in result.mappings():
        lead = _to_lead(row)
        stages.setdefault(lead.status, []).append(lead)
        if lead.status == "won" and lead.value_cents:
            won_total += lead.value_cents

    return Board(
        stages=stages,
        counts={stage: len(rows) for stage, rows in stages.items()},
        won_value_cents=won_total,
    )


@router.get("", response_model=list[Lead])
async def list_leads(
    user: CurrentUserDep,
    conn: TenantConn,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[Lead]:
    """The table view. Sortable and exportable in the browser."""
    del user
    clause = "AND status = :status" if status_filter else ""
    result = await conn.execute(
        text(
            f"""
            SELECT id, caller_name, service_address, callback_e164, job_type,
                   description, urgency, source, status, value_cents, currency,
                   lost_reason, created_at, first_touched_at, won_at,
                   contact_id, call_id,
                   extract(day FROM now() - coalesce(won_at, created_at))::int AS days,
                   (first_touched_at IS NULL AND status = 'new'
                    AND created_at < now() - make_interval(hours => 24)) AS is_stale
            FROM leads
            WHERE 1 = 1 {clause}
            ORDER BY created_at DESC
            LIMIT :limit
            """  # noqa: S608 - `clause` is a literal chosen above, never input
        ),
        {"limit": limit} | ({"status": status_filter} if status_filter else {}),
    )
    return [_to_lead(row) for row in result.mappings()]


class ValueUpdate(BaseModel):
    """What the owner typed into "What's this job worth?".

    A string, deliberately. The browser must not do the parsing — one parser,
    used by both the portal and the SMS grammar, is what keeps '3,800' and
    '38OO' behaving the same way in both places.
    """

    amount: str


@router.put("/{lead_id}/value", response_model=Lead)
async def set_value(
    lead_id: str, body: ValueUpdate, user: CurrentUserDep, conn: TenantConn
) -> Lead:
    """The only write of `leads.value_cents` from the portal.

    Refuses rather than guesses, and says why. This number is the headline of
    every report the owner judges us by.
    """
    raw = (body.amount or "").strip()

    if not raw:
        # Clearing the field is legitimate — he entered it wrong and wants it
        # blank again.
        await conn.execute(
            text("UPDATE leads SET value_cents = NULL, updated_at = now() WHERE id = :id"),
            {"id": lead_id},
        )
        return await get_lead(lead_id, user, conn)

    try:
        amount: Money = parse_owner_amount(raw)
    except MoneyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"That doesn't look like an amount: {exc}",
        ) from exc

    result = await conn.execute(
        text(
            "UPDATE leads SET value_cents = :cents, currency = :currency, "
            "updated_at = now() WHERE id = :id RETURNING id"
        ),
        {"id": lead_id, "cents": amount.cents, "currency": amount.currency},
    )
    if result.first() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such lead")

    await _record(conn, user, lead_id, f"Value set to {amount.format_whole()}")
    return await get_lead(lead_id, user, conn)


class StatusUpdate(BaseModel):
    status: Literal[
        "new", "contacted", "estimate_scheduled", "estimate_sent", "won", "lost", "spam"
    ]
    lost_reason: str | None = None


@router.put("/{lead_id}/status", response_model=Lead)
async def set_status(
    lead_id: str, body: StatusUpdate, user: CurrentUserDep, conn: TenantConn
) -> Lead:
    """Drag on desktop, tap-to-advance on mobile.

    Moving a lead out of `new` counts as touching it, which is what clears the
    red dot and the follow-up nudge. That is the right semantics: he has dealt
    with it, whatever the outcome.
    """
    result = await conn.execute(
        text(
            """
            UPDATE leads
            SET status = :status,
                lost_reason = CASE WHEN :status = 'lost' THEN :reason ELSE lost_reason END,
                won_at = CASE WHEN :status = 'won' THEN coalesce(won_at, now()) ELSE won_at END,
                first_touched_at = coalesce(first_touched_at, now()),
                updated_at = now()
            WHERE id = :id
            RETURNING id
            """
        ),
        {"id": lead_id, "status": body.status, "reason": body.lost_reason},
    )
    if result.first() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such lead")

    await _record(conn, user, lead_id, f"Status changed to {body.status}")
    return await get_lead(lead_id, user, conn)


@router.get("/{lead_id}", response_model=Lead)
async def get_lead(lead_id: str, user: CurrentUserDep, conn: TenantConn) -> Lead:
    del user
    result = await conn.execute(
        text(
            """
            SELECT id, caller_name, service_address, callback_e164, job_type,
                   description, urgency, source, status, value_cents, currency,
                   lost_reason, created_at, first_touched_at, won_at,
                   contact_id, call_id,
                   extract(day FROM now() - coalesce(won_at, created_at))::int AS days,
                   (first_touched_at IS NULL AND status = 'new'
                    AND created_at < now() - make_interval(hours => 24)) AS is_stale
            FROM leads WHERE id = :id
            """
        ),
        {"id": lead_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such lead")
    return _to_lead(row)


def _to_lead(row: Any) -> Lead:
    return Lead(
        id=str(row["id"]),
        caller_name=row["caller_name"],
        service_address=row["service_address"],
        callback_e164=row["callback_e164"],
        job_type=row["job_type"],
        description=row["description"],
        urgency=row["urgency"],
        source=row["source"],
        status=row["status"],
        value_cents=row["value_cents"],
        currency=row["currency"],
        lost_reason=row["lost_reason"],
        created_at=row["created_at"],
        first_touched_at=row["first_touched_at"],
        won_at=row["won_at"],
        days_in_stage=int(row["days"] or 0),
        is_stale=bool(row["is_stale"]),
        contact_id=str(row["contact_id"]) if row["contact_id"] else None,
        call_id=str(row["call_id"]) if row["call_id"] else None,
    )


async def _record(conn: Any, user: Any, lead_id: str, body: str) -> None:
    """Everything that changes a lead lands in its thread, with who did it."""
    from mabel_db.queries import events as events_q

    await events_q.append(
        conn,
        tenant_id=user.tenant_id,
        lead_id=lead_id,
        kind="status_change",
        direction="internal",
        body=body,
        actor_user_id=user.user_id,
        payload={"channel": "portal"},
    )
