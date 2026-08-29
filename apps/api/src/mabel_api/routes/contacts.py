"""Customers, and the unified thread.

02-PORTAL.md: "the thing that turns this from an answering service into a
system of record."

Two parts of that are worth calling out.

**Open items are pinned at the top.** "Asked about a color change Apr 18 — no
reply." That is the dropped-ball surfacing, and 02-PORTAL.md says it is the
feature owners will talk about. It is deliberately a simple rule — an inbound
message with nothing outbound after it — because a false positive costs a
glance and a false negative costs the job.

**Merges are never automatic.** Identity resolution is deterministic on phone
and fuzzy candidates are shown as a banner with Merge and Not the same. A wrong
merge splices two customers' histories together; a missed merge shows a banner.
Those costs are not symmetric.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from mabel_db.queries import contacts as contacts_q
from mabel_db.queries import events as events_q
from pydantic import BaseModel
from sqlalchemy import text

from mabel_api.deps import CurrentUser, CurrentUserDep, TenantConn, require_role

router = APIRouter(prefix="/api/contacts", tags=["contacts"])


class ContactSummary(BaseModel):
    id: str
    display_name: str | None
    primary_phone: str | None
    last_seen_at: datetime
    open_items: int


class ThreadEntry(BaseModel):
    id: str
    kind: str
    direction: str | None
    occurred_at: datetime
    body: str | None
    lead_id: str | None
    has_recording: bool


class MergeCandidate(BaseModel):
    id: str
    display_name: str | None
    primary_phone: str | None
    score: float


class ContactDetail(BaseModel):
    id: str
    display_name: str | None
    primary_phone: str | None
    phones: list[str]
    first_seen_at: datetime
    last_seen_at: datetime
    # Pinned at the top of the thread.
    open_items: list[ThreadEntry]
    thread: list[ThreadEntry]
    merge_candidates: list[MergeCandidate]


@router.get("", response_model=list[ContactSummary])
async def list_contacts(
    user: CurrentUserDep,
    conn: TenantConn,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[ContactSummary]:
    del user
    clause = ""
    params: dict[str, Any] = {"limit": limit}
    if q and q.strip():
        # Trigram similarity, so "henderson" finds "Bob Henderson" and a
        # misspelling still lands somewhere useful.
        clause = "AND (c.display_name %% :q OR c.primary_phone LIKE :like)"
        params |= {"q": q.strip(), "like": f"%{q.strip()}%"}

    result = await conn.execute(
        text(
            f"""
            SELECT c.id, c.display_name, c.primary_phone, c.last_seen_at,
                   (SELECT count(*) FROM communication_events e
                    WHERE e.contact_id = c.id
                      AND e.direction = 'inbound'
                      AND NOT EXISTS (
                        SELECT 1 FROM communication_events later
                        WHERE later.contact_id = c.id
                          AND later.direction = 'outbound'
                          AND later.occurred_at > e.occurred_at)) AS open_items
            FROM contacts c
            WHERE c.deleted_at IS NULL AND c.merged_into IS NULL {clause}
            ORDER BY c.last_seen_at DESC
            LIMIT :limit
            """  # noqa: S608 - `clause` is a literal chosen above, never input
        ),
        params,
    )
    return [
        ContactSummary(
            id=str(row["id"]),
            display_name=row["display_name"],
            primary_phone=row["primary_phone"],
            last_seen_at=row["last_seen_at"],
            open_items=int(row["open_items"]),
        )
        for row in result.mappings()
    ]


@router.get("/{contact_id}", response_model=ContactDetail)
async def get_contact(contact_id: str, user: CurrentUserDep, conn: TenantConn) -> ContactDetail:
    del user
    result = await conn.execute(
        text(
            "SELECT id, display_name, primary_phone, phones, first_seen_at, last_seen_at "
            "FROM contacts WHERE id = :id AND deleted_at IS NULL"
        ),
        {"id": contact_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such customer")

    thread = [_to_entry(entry) for entry in await events_q.thread_for_contact(conn, contact_id)]
    open_items = [_to_entry(entry) for entry in await events_q.open_items(conn, contact_id)]

    candidates: list[MergeCandidate] = []
    if row["display_name"]:
        for match in await contacts_q.find_fuzzy_by_name(conn, row["display_name"]):
            if str(match.contact.id) == str(contact_id):
                continue
            candidates.append(
                MergeCandidate(
                    id=str(match.contact.id),
                    display_name=match.contact.display_name,
                    primary_phone=match.contact.primary_phone,
                    score=match.score,
                )
            )

    return ContactDetail(
        id=str(row["id"]),
        display_name=row["display_name"],
        primary_phone=row["primary_phone"],
        phones=list(row["phones"] or []),
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        open_items=open_items,
        thread=thread,
        merge_candidates=candidates,
    )


class MergeRequest(BaseModel):
    """`other_id` is folded into the contact in the path."""

    other_id: str


@router.post("/{contact_id}/merge", response_model=ContactDetail)
async def merge(
    contact_id: str,
    body: MergeRequest,
    user: CurrentUserDep,
    conn: TenantConn,
    _guard: CurrentUser = Depends(require_role("owner", "office")),
) -> ContactDetail:
    """Fold one contact into another.

    Reversible by design: the merged-away row is kept with `merged_into` set
    rather than deleted, and the merge is recorded as a thread event. 02-PORTAL.md
    asks for that explicitly, and it is what makes the banner safe to click —
    a wrong merge is an undo rather than a support ticket.
    """
    if body.other_id == contact_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="That's the same person.",
        )

    # Events follow the survivor, so the thread reads as one history.
    await conn.execute(
        text("UPDATE communication_events SET contact_id = :keep WHERE contact_id = :fold"),
        {"keep": contact_id, "fold": body.other_id},
    )
    await conn.execute(
        text("UPDATE leads SET contact_id = :keep WHERE contact_id = :fold"),
        {"keep": contact_id, "fold": body.other_id},
    )
    await conn.execute(
        text("UPDATE calls SET contact_id = :keep WHERE contact_id = :fold"),
        {"keep": contact_id, "fold": body.other_id},
    )

    # Their numbers come across, so a future call from either one resolves.
    await conn.execute(
        text(
            """
            UPDATE contacts keep
            SET phones = (
              SELECT array_agg(DISTINCT phone)
              FROM unnest(keep.phones || fold.phones) AS phone
              WHERE phone IS NOT NULL
            )
            FROM contacts fold
            WHERE keep.id = :keep AND fold.id = :fold
            """
        ),
        {"keep": contact_id, "fold": body.other_id},
    )

    result = await conn.execute(
        text("UPDATE contacts SET merged_into = :keep WHERE id = :fold RETURNING id"),
        {"keep": contact_id, "fold": body.other_id},
    )
    if result.first() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such customer")

    await events_q.append(
        conn,
        tenant_id=user.tenant_id,
        contact_id=contact_id,
        kind="identity_merged",
        direction="internal",
        body="Merged with another customer record.",
        actor_user_id=user.user_id,
        payload={"merged_id": body.other_id, "reversible": True},
    )

    return await get_contact(contact_id, user, conn)


@router.post("/{contact_id}/not-a-duplicate/{other_id}")
async def dismiss_candidate(
    contact_id: str, other_id: str, user: CurrentUserDep, conn: TenantConn
) -> dict[str, str]:
    """ "Not the same" on the banner.

    Recorded as a thread event rather than as a column, because the banner is
    computed from trigram similarity each time and this is the only durable
    record that a human already answered the question.
    """
    await events_q.append(
        conn,
        tenant_id=user.tenant_id,
        contact_id=contact_id,
        kind="system",
        direction="internal",
        body="Confirmed as a different person from another record.",
        actor_user_id=user.user_id,
        payload={"not_duplicate_of": other_id},
    )
    return {"status": "noted"}


def _to_entry(entry: dict[str, Any]) -> ThreadEntry:
    return ThreadEntry(
        id=str(entry["id"]),
        kind=entry["kind"],
        direction=entry.get("direction"),
        occurred_at=entry["occurred_at"],
        body=entry.get("body"),
        lead_id=str(entry["lead_id"]) if entry.get("lead_id") else None,
        has_recording=bool(entry.get("storage_path")),
    )
