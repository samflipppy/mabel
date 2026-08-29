"""The Calls screen. The transcript archive.

02-PORTAL.md calls full-text search across transcripts "the feature nobody else
offers", and it is the one that makes the archive worth having: "search for
that guy who called about the water heater" and it finds him.

The `to_tsvector` index from 01-SCHEMA.sql is what makes it fast. The query
below uses exactly the expression the index was built on, because a query that
differs by so much as the `coalesce` will not use it and will seq-scan every
transcript the tenant has.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import text

from mabel_api.deps import CurrentUserDep, TenantConn

router = APIRouter(prefix="/api/calls", tags=["calls"])

PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# Long enough to be a search, short enough not to be a paste of a transcript.
MAX_QUERY_CHARS = 200


class CallSummary(BaseModel):
    id: str
    started_at: datetime
    caller: str | None
    from_e164: str | None
    duration_sec: int | None
    outcome: str | None
    qa_flags: list[str]
    has_recording: bool
    # Only present on a search: the matching line, with the terms marked.
    excerpt: str | None = None


class Turn(BaseModel):
    role: str
    text: str
    started_ms: int | None = None
    ended_ms: int | None = None


class ToolCall(BaseModel):
    tool: str
    ok: bool
    mutating: bool = False
    duration_ms: int | None = None


class Extraction(BaseModel):
    """The structured extraction panel: what she actually got."""

    name: str | None
    address: str | None
    phone: str | None
    job_type: str | None
    urgency: str | None
    source: str | None


class CallDetail(BaseModel):
    id: str
    started_at: datetime
    ended_at: datetime | None
    duration_sec: int | None
    caller: str | None
    from_e164: str | None
    to_e164: str | None
    outcome: str | None
    qa_flags: list[str]
    qa_summary: str | None
    turns: list[Turn]
    summary: str | None
    # 02-PORTAL.md: "what Mabel actually did during the call... This builds
    # trust; they can see the machine working."
    tool_trace: list[ToolCall]
    extraction: Extraction | None
    lead_id: str | None
    contact_id: str | None
    has_recording: bool


class CallPage(BaseModel):
    calls: list[CallSummary]
    total: int
    has_more: bool


@router.get("", response_model=CallPage)
async def list_calls(
    user: CurrentUserDep,
    conn: TenantConn,
    q: Annotated[str | None, Query(max_length=MAX_QUERY_CHARS)] = None,
    outcome: Annotated[str | None, Query()] = None,
    emergency_only: Annotated[bool, Query()] = False,
    flagged_only: Annotated[bool, Query()] = False,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CallPage:
    """The call log, with the filters from 02-PORTAL.md.

    Note what is not a parameter: a tenant. RLS scopes this, and there is no
    argument through which another tenant's calls could be requested.
    """
    del user

    clauses = ["1 = 1"]
    params: dict[str, Any] = {"limit": limit + 1, "offset": offset}

    if q and q.strip():
        # The same expression the index was built on. Differing from it by even
        # the coalesce means a sequential scan over every transcript.
        clauses.append(
            "EXISTS (SELECT 1 FROM transcripts t WHERE t.call_id = c.id "
            "AND to_tsvector('english', coalesce(t.full_text,'')) "
            "@@ plainto_tsquery('english', :q))"
        )
        params["q"] = q.strip()
    if outcome:
        clauses.append("c.outcome = :outcome")
        params["outcome"] = outcome
    if emergency_only:
        clauses.append("c.outcome = 'emergency'")
    if flagged_only:
        clauses.append("array_length(c.qa_flags, 1) > 0")
    if since:
        clauses.append("c.started_at >= :since")
        params["since"] = since
    if until:
        clauses.append("c.started_at <= :until")
        params["until"] = until

    where = " AND ".join(clauses)

    result = await conn.execute(
        text(
            f"""
            SELECT c.id, c.started_at, c.from_e164, c.duration_sec, c.outcome,
                   c.qa_flags, c.recording_path IS NOT NULL AS has_recording,
                   ct.display_name AS caller,
                   CASE WHEN :q_present THEN (
                     SELECT ts_headline('english', coalesce(t.full_text, ''),
                                        plainto_tsquery('english', :q),
                                        'MaxWords=18, MinWords=8, MaxFragments=1')
                     FROM transcripts t WHERE t.call_id = c.id LIMIT 1
                   ) END AS excerpt
            FROM calls c
            LEFT JOIN contacts ct ON ct.id = c.contact_id
            WHERE {where}
            ORDER BY c.started_at DESC
            LIMIT :limit OFFSET :offset
            """  # noqa: S608 - `where` is assembled from literals above, never from input
        ),
        params | {"q_present": bool(q and q.strip()), "q": params.get("q", "")},
    )
    rows = [dict(row) for row in result.mappings()]

    has_more = len(rows) > limit
    rows = rows[:limit]

    counted = await conn.execute(
        text(f"SELECT count(*) FROM calls c WHERE {where}"),  # noqa: S608 - same
        {k: v for k, v in params.items() if k not in {"limit", "offset"}},
    )

    return CallPage(
        calls=[
            CallSummary(
                id=str(row["id"]),
                started_at=row["started_at"],
                caller=row["caller"],
                from_e164=row["from_e164"],
                duration_sec=row["duration_sec"],
                outcome=row["outcome"],
                qa_flags=list(row["qa_flags"] or []),
                has_recording=bool(row["has_recording"]),
                excerpt=row.get("excerpt"),
            )
            for row in rows
        ],
        total=int(counted.scalar_one()),
        has_more=has_more,
    )


@router.get("/{call_id}", response_model=CallDetail)
async def get_call(call_id: str, user: CurrentUserDep, conn: TenantConn) -> CallDetail:
    del user
    result = await conn.execute(
        text(
            """
            SELECT c.id, c.started_at, c.ended_at, c.duration_sec, c.from_e164, c.to_e164,
                   c.outcome, c.qa_flags, c.lead_id, c.contact_id,
                   c.recording_path IS NOT NULL AS has_recording,
                   ct.display_name AS caller,
                   t.turns, t.summary, t.tool_trace,
                   l.caller_name, l.service_address, l.callback_e164, l.job_type,
                   l.urgency, l.source
            FROM calls c
            LEFT JOIN contacts ct ON ct.id = c.contact_id
            LEFT JOIN transcripts t ON t.call_id = c.id
            LEFT JOIN leads l ON l.id = c.lead_id
            WHERE c.id = :id
            """
        ),
        {"id": call_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        # A call in another tenant returns 404 rather than 403. Telling the
        # caller a row exists but is not theirs is itself information.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such call")

    from mabel_media.qa import summarise

    extraction = None
    if row["caller_name"] or row["job_type"]:
        extraction = Extraction(
            name=row["caller_name"],
            address=row["service_address"],
            phone=row["callback_e164"],
            job_type=row["job_type"],
            urgency=row["urgency"],
            source=row["source"],
        )

    return CallDetail(
        id=str(row["id"]),
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        duration_sec=row["duration_sec"],
        caller=row["caller"],
        from_e164=row["from_e164"],
        to_e164=row["to_e164"],
        outcome=row["outcome"],
        qa_flags=list(row["qa_flags"] or []),
        qa_summary=summarise(list(row["qa_flags"] or [])),
        turns=[Turn(**turn) for turn in (row["turns"] or [])],
        summary=row["summary"],
        tool_trace=[
            ToolCall(
                tool=entry.get("tool", "?"),
                ok=bool(entry.get("ok", True)),
                mutating=bool(entry.get("mutating", False)),
                duration_ms=entry.get("duration_ms"),
            )
            for entry in (row["tool_trace"] or [])
        ],
        extraction=extraction,
        lead_id=str(row["lead_id"]) if row["lead_id"] else None,
        contact_id=str(row["contact_id"]) if row["contact_id"] else None,
        has_recording=bool(row["has_recording"]),
    )


class MarkRequest(BaseModel):
    outcome: Literal["spam", "wrong_number", "lead", "existing_customer"]


@router.post("/{call_id}/outcome", response_model=CallDetail)
async def set_outcome(
    call_id: str, body: MarkRequest, user: CurrentUserDep, conn: TenantConn
) -> CallDetail:
    """Re-label a call. The office manager knows things the model does not."""
    result = await conn.execute(
        text("UPDATE calls SET outcome = :outcome WHERE id = :id RETURNING id"),
        {"id": call_id, "outcome": body.outcome},
    )
    if result.first() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such call")
    return await get_call(call_id, user, conn)


@router.post("/{call_id}/reviewed")
async def mark_reviewed(call_id: str, user: CurrentUserDep, conn: TenantConn) -> dict[str, str]:
    """Clear a QA flag off the needs-you list.

    The flags stay on the row — they are evidence, and a call that quoted a
    price still did. What changes is that a human has looked.
    """
    del user
    result = await conn.execute(
        text("UPDATE calls SET qa_reviewed_at = now() WHERE id = :id RETURNING id"),
        {"id": call_id},
    )
    if result.first() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such call")
    return {"status": "reviewed"}
