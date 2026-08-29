"""Reports. The retention artifact.

02-PORTAL.md's example is the specification, and the last line of it is the
point of the whole product:

    You paid $299. Five won jobs came to $14,600.

Everything in that sentence is a count or a sum of integer cents the owner
entered himself. Nothing is estimated and nothing is produced by a model —
which is exactly why he believes it.

**"Slow months say so. A report that's always good news gets ignored."** The
narrative builder below has a branch for a bad month and it does not soften it.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from mabel_domain.money import Money
from pydantic import BaseModel
from sqlalchemy import text

from mabel_api.deps import CurrentUserDep, TenantConn

router = APIRouter(prefix="/api/reports", tags=["reports"])


class MonthlyReport(BaseModel):
    period_start: date
    period_end: date
    calls_answered: int
    leads_created: int
    emergencies: int
    jobs_won: int
    # Cents. Formatted in the browser, like everywhere else.
    won_value_cents: int
    source_breakdown: dict[str, int]
    untouched_leads: list[dict[str, Any]]
    pdf_path: str | None
    sent_at: datetime | None
    # Pre-rendered sentences, so the portal and the PDF read identically.
    narrative: list[str]


class UsageDay(BaseModel):
    day: date
    calls_answered: int
    voice_minutes: float
    sms_sent: int
    cost_cents: int


class Usage(BaseModel):
    days: list[UsageDay]
    minutes_used: float
    minutes_included: int | None
    # Our own cost, for the transparency 02-PORTAL.md asks for. Cents.
    cost_cents: int


class SourceMonth(BaseModel):
    month: date
    sources: dict[str, int]


@router.get("/monthly", response_model=list[MonthlyReport])
async def list_reports(user: CurrentUserDep, conn: TenantConn) -> list[MonthlyReport]:
    del user
    result = await conn.execute(
        text(
            """
            SELECT period_start, period_end, calls_answered, leads_created,
                   emergencies, jobs_won, won_value_cents, source_breakdown,
                   untouched_leads, pdf_path, sent_at
            FROM monthly_reports
            ORDER BY period_start DESC
            LIMIT 24
            """
        )
    )
    reports = []
    for row in result.mappings():
        data = dict(row)
        reports.append(MonthlyReport(**data, narrative=build_narrative(data)))
    return reports


@router.get("/monthly/{period_start}", response_model=MonthlyReport)
async def get_report(period_start: date, user: CurrentUserDep, conn: TenantConn) -> MonthlyReport:
    del user
    result = await conn.execute(
        text(
            """
            SELECT period_start, period_end, calls_answered, leads_created,
                   emergencies, jobs_won, won_value_cents, source_breakdown,
                   untouched_leads, pdf_path, sent_at
            FROM monthly_reports WHERE period_start = :start
            """
        ),
        {"start": period_start},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no report for that month"
        )
    data = dict(row)
    return MonthlyReport(**data, narrative=build_narrative(data))


def build_narrative(report: dict[str, Any]) -> list[str]:
    """The sentences from 02-PORTAL.md, built from the figures.

    Deterministic. A language model writing these would be an LLM producing a
    sentence containing a dollar figure, which is the thing invariant 4 exists
    to prevent — and it would also make the report sound different every month,
    which is how a customer stops trusting it.
    """
    month = report["period_start"].strftime("%B %Y")
    calls = int(report["calls_answered"])
    leads = int(report["leads_created"])
    won = int(report["jobs_won"])
    value = Money(int(report["won_value_cents"]))
    emergencies = int(report["emergencies"])
    waiting = list(report["untouched_leads"] or [])

    lines = [month]

    if calls == 0:
        # The honest version of a bad month. No softening: a report that is
        # always good news gets ignored, and then the good months do not land
        # either.
        lines.append("Mabel didn't answer any calls this month.")
        lines.append(
            "If that's a surprise, check your call forwarding on the Settings "
            "screen — that's usually what it is."
        )
        return lines

    lines.append(
        f"Mabel answered {calls} call{'s' if calls != 1 else ''} after hours. "
        "Before Mabel, those went to voicemail."
    )

    if leads:
        if won:
            lines.append(
                f"{leads} became lead{'s' if leads != 1 else ''}. "
                f"You marked {won} won: {value.format_whole()}."
            )
        else:
            lines.append(
                f"{leads} became lead{'s' if leads != 1 else ''}. "
                "None marked won yet — the value field on each lead is what "
                "fills this in."
            )
    else:
        lines.append("None of them turned into leads this month.")

    if emergencies:
        lines.append(f"Emergencies handled: {emergencies}")

    sources = report["source_breakdown"] or {}
    if sources:
        ranked = sorted(sources.items(), key=lambda pair: -pair[1])
        lines.append(
            "Where they came from: "
            + " · ".join(f"{name.title()} {count}" for name, count in ranked[:5])
        )

    if waiting:
        oldest = min(entry["since"] for entry in waiting if entry.get("since"))
        days = (date.today() - date.fromisoformat(oldest)).days
        lines.append(
            f"Still waiting on you: {len(waiting)} lead"
            f"{'s' if len(waiting) != 1 else ''}, oldest {days} days."
        )

    return lines


@router.get("/usage", response_model=Usage)
async def get_usage(
    user: CurrentUserDep,
    conn: TenantConn,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> Usage:
    """02-PORTAL.md: "Transparent, because surprise overage bills are how
    answering services lose customers." """
    del user
    result = await conn.execute(
        text(
            """
            SELECT day, calls_answered, voice_minutes, sms_sent, cost_cents
            FROM usage_daily
            WHERE day > current_date - make_interval(days => :days)
            ORDER BY day
            """
        ),
        {"days": days},
    )
    rows = [
        UsageDay(
            day=row["day"],
            calls_answered=int(row["calls_answered"]),
            voice_minutes=float(row["voice_minutes"]),
            sms_sent=int(row["sms_sent"]),
            cost_cents=int(row["cost_cents"]),
        )
        for row in result.mappings()
    ]

    included = await conn.execute(
        text("SELECT included_minutes FROM subscriptions ORDER BY created_at DESC LIMIT 1")
    )

    return Usage(
        days=rows,
        minutes_used=round(sum(row.voice_minutes for row in rows), 2),
        minutes_included=included.scalar_one_or_none(),
        cost_cents=sum(row.cost_cents for row in rows),
    )


@router.get("/sources", response_model=list[SourceMonth])
async def lead_sources(user: CurrentUserDep, conn: TenantConn) -> list[SourceMonth]:
    """Twelve months of where calls come from.

    02-PORTAL.md: "Most contractors have never had this data and it's genuinely
    useful to them." It is also the only screen that argues for the price — a
    contractor who can see that referrals outrun his truck signage will spend
    differently.
    """
    del user
    result = await conn.execute(
        text(
            """
            SELECT date_trunc('month', created_at)::date AS month,
                   coalesce(source, 'unknown') AS source,
                   count(*) AS n
            FROM leads
            WHERE created_at > now() - interval '12 months'
            GROUP BY 1, 2
            ORDER BY 1
            """
        )
    )
    months: dict[date, dict[str, int]] = {}
    for row in result.mappings():
        months.setdefault(row["month"], {})[row["source"]] = int(row["n"])
    return [SourceMonth(month=month, sources=sources) for month, sources in months.items()]
