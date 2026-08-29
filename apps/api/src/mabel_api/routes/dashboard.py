"""The Dashboard screen's data.

02-PORTAL.md: "Answers 'what happened and what needs me.'" The needs-you list
sits above everything else, so it is computed first and is the part that must
never be wrong.

**Every dollar figure here is a sum of `leads.value_cents`** — integers the
owner typed in himself. `value_won_cents` is returned as cents and formatted in
the browser, so no rounding happens twice and no float appears anywhere in the
path.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from mabel_api.deps import CurrentUserDep, TenantConn

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# 02-PORTAL.md: leads with no callback in 24h, emergencies from the last 48h,
# any call flagged in QA.
UNTOUCHED_HOURS = 24
EMERGENCY_WINDOW_HOURS = 48


class Card(BaseModel):
    """One of the four big numbers, with its delta against last month."""

    label: str
    value: int
    # Cents when this card is money, so the browser formats once.
    is_money: bool = False
    previous: int = 0

    @property
    def delta(self) -> int:
        return self.value - self.previous


class NeedsYouRow(BaseModel):
    kind: str  # untouched_lead | emergency | qa_flag
    id: str
    name: str | None
    phone_e164: str | None
    summary: str
    since: datetime
    hours_waiting: int
    answered: bool | None = None


class RecentCall(BaseModel):
    id: str
    caller: str | None
    from_e164: str | None
    started_at: datetime
    duration_sec: int | None
    outcome: str | None
    qa_flags: list[str]
    has_recording: bool


class DayBar(BaseModel):
    day: date
    total: int
    after_hours: int


class Dashboard(BaseModel):
    cards: list[Card]
    needs_you: list[NeedsYouRow]
    recent_calls: list[RecentCall]
    this_week: list[DayBar]
    # Empty state: "Mabel's live and listening. Calls will show up here."
    is_empty: bool


@router.get("", response_model=Dashboard)
async def get_dashboard(user: CurrentUserDep, conn: TenantConn) -> Dashboard:
    del user  # the tenant is already bound into `conn`
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous_start = (month_start - timedelta(days=1)).replace(day=1)

    cards = await _cards(conn, month_start, previous_start)
    needs_you = await _needs_you(conn, now)
    recent = await _recent_calls(conn)
    week = await _this_week(conn)

    return Dashboard(
        cards=cards,
        needs_you=needs_you,
        recent_calls=recent,
        this_week=week,
        is_empty=not recent and all(card.value == 0 for card in cards),
    )


async def _cards(conn: Any, month_start: datetime, previous_start: datetime) -> list[Card]:
    result = await conn.execute(
        text(
            """
            SELECT
              count(*) FILTER (WHERE c.started_at >= :month) AS calls_now,
              count(*) FILTER (WHERE c.started_at >= :prev AND c.started_at < :month)
                AS calls_prev
            FROM calls c
            """
        ),
        {"month": month_start, "prev": previous_start},
    )
    calls = result.mappings().one()

    result = await conn.execute(
        text(
            """
            SELECT
              count(*) FILTER (WHERE created_at >= :month) AS leads_now,
              count(*) FILTER (WHERE created_at >= :prev AND created_at < :month)
                AS leads_prev,
              count(*) FILTER (WHERE created_at >= :month AND urgency = 'emergency')
                AS emergencies_now,
              count(*) FILTER (WHERE created_at >= :prev AND created_at < :month
                               AND urgency = 'emergency') AS emergencies_prev,
              coalesce(sum(value_cents) FILTER (WHERE won_at >= :month), 0) AS won_now,
              coalesce(sum(value_cents) FILTER (WHERE won_at >= :prev AND won_at < :month), 0)
                AS won_prev
            FROM leads
            """
        ),
        {"month": month_start, "prev": previous_start},
    )
    leads = result.mappings().one()

    return [
        Card(
            label="Calls answered", value=int(calls["calls_now"]), previous=int(calls["calls_prev"])
        ),
        Card(
            label="Leads captured", value=int(leads["leads_now"]), previous=int(leads["leads_prev"])
        ),
        Card(
            label="Emergencies",
            value=int(leads["emergencies_now"]),
            previous=int(leads["emergencies_prev"]),
        ),
        Card(
            label="Value won",
            value=int(leads["won_now"]),
            previous=int(leads["won_prev"]),
            # Cents. The browser formats it, so no figure is rounded twice.
            is_money=True,
        ),
    ]


async def _needs_you(conn: Any, now: datetime) -> list[NeedsYouRow]:
    """The action list. Sorted by age, oldest first, because the oldest is the
    one going cold."""
    rows: list[NeedsYouRow] = []

    untouched = await conn.execute(
        text(
            """
            SELECT id, caller_name, callback_e164, job_type, created_at,
                   extract(epoch FROM now() - created_at) / 3600 AS hours
            FROM leads
            WHERE first_touched_at IS NULL
              AND status = 'new'
              AND created_at < now() - make_interval(hours => :hours)
            ORDER BY created_at
            """
        ),
        {"hours": UNTOUCHED_HOURS},
    )
    for row in untouched.mappings():
        rows.append(
            NeedsYouRow(
                kind="untouched_lead",
                id=str(row["id"]),
                name=row["caller_name"],
                phone_e164=row["callback_e164"],
                summary=row["job_type"] or "no detail",
                since=row["created_at"],
                hours_waiting=int(row["hours"]),
            )
        )

    emergencies = await conn.execute(
        text(
            """
            SELECT l.id, l.caller_name, l.callback_e164, l.job_type, l.created_at,
                   l.first_touched_at IS NOT NULL AS answered,
                   extract(epoch FROM now() - l.created_at) / 3600 AS hours
            FROM leads l
            WHERE l.urgency = 'emergency'
              AND l.created_at > now() - make_interval(hours => :hours)
            ORDER BY l.created_at DESC
            """
        ),
        {"hours": EMERGENCY_WINDOW_HOURS},
    )
    for row in emergencies.mappings():
        rows.append(
            NeedsYouRow(
                kind="emergency",
                id=str(row["id"]),
                name=row["caller_name"],
                phone_e164=row["callback_e164"],
                summary=row["job_type"] or "emergency",
                since=row["created_at"],
                hours_waiting=int(row["hours"]),
                # 02-PORTAL.md wants "whether they were answered" shown, not
                # just that they happened.
                answered=bool(row["answered"]),
            )
        )

    flagged = await conn.execute(
        text(
            """
            SELECT id, from_e164, started_at, qa_flags,
                   extract(epoch FROM now() - started_at) / 3600 AS hours
            FROM calls
            WHERE array_length(qa_flags, 1) > 0
              AND qa_reviewed_at IS NULL
            ORDER BY started_at DESC
            LIMIT 20
            """
        )
    )
    for row in flagged.mappings():
        from mabel_media.qa import summarise

        rows.append(
            NeedsYouRow(
                kind="qa_flag",
                id=str(row["id"]),
                name=None,
                phone_e164=row["from_e164"],
                summary=summarise(list(row["qa_flags"])) or "flagged for review",
                since=row["started_at"],
                hours_waiting=int(row["hours"]),
            )
        )

    # Emergencies first, then by age. An unanswered emergency outranks a
    # nine-day-old routine lead however long the lead has been sitting.
    rows.sort(key=lambda r: (r.kind != "emergency", -r.hours_waiting))
    return rows


async def _recent_calls(conn: Any, limit: int = 10) -> list[RecentCall]:
    result = await conn.execute(
        text(
            """
            SELECT c.id, c.from_e164, c.started_at, c.duration_sec, c.outcome,
                   c.qa_flags, c.recording_path IS NOT NULL AS has_recording,
                   ct.display_name AS caller
            FROM calls c
            LEFT JOIN contacts ct ON ct.id = c.contact_id
            ORDER BY c.started_at DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    return [
        RecentCall(
            id=str(row["id"]),
            caller=row["caller"],
            from_e164=row["from_e164"],
            started_at=row["started_at"],
            duration_sec=row["duration_sec"],
            outcome=row["outcome"],
            qa_flags=list(row["qa_flags"] or []),
            has_recording=bool(row["has_recording"]),
        )
        for row in result.mappings()
    ]


async def _this_week(conn: Any) -> list[DayBar]:
    """Calls per day, split after-hours versus business-hours.

    The split is computed against the live config's business hours in the
    tenant's own timezone, in SQL, so a tenant in Denver does not get
    Cleveland's idea of after hours.
    """
    result = await conn.execute(
        text(
            """
            WITH tz AS (SELECT timezone FROM tenants LIMIT 1),
            hours AS (SELECT business_hours FROM agent_configs WHERE is_live LIMIT 1)
            SELECT
              (c.started_at AT TIME ZONE tz.timezone)::date AS day,
              count(*) AS total,
              count(*) FILTER (
                WHERE NOT (
                  -- Inside the configured window for that weekday.
                  (hours.business_hours
                     -> lower(to_char(c.started_at AT TIME ZONE tz.timezone, 'Dy'))
                     ->> 'open') IS NOT NULL
                  AND (c.started_at AT TIME ZONE tz.timezone)::time
                      >= (hours.business_hours
                          -> lower(to_char(c.started_at AT TIME ZONE tz.timezone, 'Dy'))
                          ->> 'open')::time
                  AND (c.started_at AT TIME ZONE tz.timezone)::time
                      < (hours.business_hours
                         -> lower(to_char(c.started_at AT TIME ZONE tz.timezone, 'Dy'))
                         ->> 'close')::time
                )
              ) AS after_hours
            FROM calls c, tz, hours
            WHERE c.started_at > now() - interval '7 days'
            GROUP BY 1
            ORDER BY 1
            """
        )
    )
    return [
        DayBar(day=row["day"], total=int(row["total"]), after_hours=int(row["after_hours"]))
        for row in result.mappings()
    ]
