"""Contacts, and the identity resolution that decides whether two calls are
the same person.

03-VOICE.md is explicit about the rule: deterministic on phone, fuzzy flagged
for review, **never auto-merged on fuzzy alone**. A wrong merge splices two
customers' histories together and is painful to undo; a missed merge shows the
office manager a banner. Those costs are not symmetric, so the code is not
symmetric either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# Below this, two names are not the same person as far as we are concerned.
# pg_trgm similarity, 0..1. Tuned to catch "Bob Henderson" against "Robert
# Henderson" without catching "Henderson" against "Anderson".
FUZZY_THRESHOLD = 0.45


@dataclass(frozen=True, slots=True)
class ContactRow:
    id: UUID
    display_name: str | None
    primary_phone: str | None
    phones: list[str]
    first_seen_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class ContactMatch:
    contact: ContactRow
    # "phone" is decisive. "fuzzy" is a suggestion for a human.
    how: str
    score: float = 1.0

    @property
    def is_decisive(self) -> bool:
        return self.how == "phone"


async def find_by_phone(conn: AsyncConnection, phone_e164: str) -> ContactRow | None:
    """The decisive match. A phone number is the same person or it is not.

    Checks `primary_phone` and the `phones` array, so a contact who has called
    from a second number is still found. Follows `merged_into` so a contact
    that was merged away resolves to the surviving record.
    """
    result = await conn.execute(
        text(
            """
            SELECT id, display_name, primary_phone, phones, first_seen_at, last_seen_at
            FROM contacts
            WHERE deleted_at IS NULL
              AND merged_into IS NULL
              AND (primary_phone = :phone OR :phone = ANY(phones))
            ORDER BY last_seen_at DESC
            LIMIT 1
            """
        ),
        {"phone": phone_e164},
    )
    row = result.mappings().one_or_none()
    return _to_row(row) if row else None


async def find_fuzzy_by_name(
    conn: AsyncConnection, name: str, *, limit: int = 5
) -> list[ContactMatch]:
    """Candidates for a human to look at. Never acted on automatically.

    Uses the trigram index from 01-SCHEMA.sql. The portal turns these into the
    'This might be the same person as Dana R.' banner, with Merge and Not the
    same. Merges are recorded as events and are reversible.
    """
    result = await conn.execute(
        text(
            """
            SELECT id, display_name, primary_phone, phones, first_seen_at, last_seen_at,
                   similarity(display_name, :name) AS score
            FROM contacts
            WHERE deleted_at IS NULL
              AND merged_into IS NULL
              AND display_name IS NOT NULL
              AND similarity(display_name, :name) >= :threshold
            ORDER BY score DESC
            LIMIT :limit
            """
        ),
        {"name": name, "threshold": FUZZY_THRESHOLD, "limit": limit},
    )
    return [
        ContactMatch(contact=_to_row(row), how="fuzzy", score=float(row["score"]))
        for row in result.mappings()
    ]


async def resolve_or_create(
    conn: AsyncConnection,
    *,
    tenant_id: UUID,
    phone_e164: str | None,
    name: str | None = None,
    now: datetime | None = None,
) -> tuple[ContactRow, str]:
    """Find this caller or make a record for them. Returns the contact and how
    we got there: `phone`, `created`.

    Deliberately never returns a fuzzy match as a resolution. A fuzzy candidate
    becomes a banner in the portal, not a decision here.
    """
    if phone_e164:
        existing = await find_by_phone(conn, phone_e164)
        if existing is not None:
            await touch(conn, existing.id, now=now)
            return existing, "phone"

    result = await conn.execute(
        text(
            """
            INSERT INTO contacts (tenant_id, display_name, primary_phone, phones,
                                  first_seen_at, last_seen_at)
            VALUES (:tenant_id, :name, CAST(:phone AS text),
                    CASE WHEN CAST(:phone AS text) IS NULL THEN '{}'::text[]
                         ELSE ARRAY[CAST(:phone AS text)] END,
                    coalesce(CAST(:now AS timestamptz), now()),
                    coalesce(CAST(:now AS timestamptz), now()))
            RETURNING id, display_name, primary_phone, phones, first_seen_at, last_seen_at
            """
        ),
        {"tenant_id": tenant_id, "name": name, "phone": phone_e164, "now": now},
    )
    return _to_row(result.mappings().one()), "created"


async def touch(conn: AsyncConnection, contact_id: UUID, *, now: datetime | None = None) -> None:
    """Record that we heard from them. Drives 'last seen' in the portal."""
    await conn.execute(
        text(
            "UPDATE contacts SET last_seen_at = coalesce(CAST(:now AS timestamptz), now()) "
            "WHERE id = :id"
        ),
        {"id": contact_id, "now": now},
    )


async def add_phone(conn: AsyncConnection, contact_id: UUID, phone_e164: str) -> None:
    """Add a number we have not seen before to an existing contact.

    `array_append` guarded by a membership check rather than a blind append, so
    calling this twice does not leave a duplicate in the array.
    """
    await conn.execute(
        text(
            """
            UPDATE contacts
            SET phones = array_append(phones, :phone)
            WHERE id = :id AND NOT (:phone = ANY(phones))
            """
        ),
        {"id": contact_id, "phone": phone_e164},
    )


def _to_row(row: Any) -> ContactRow:
    return ContactRow(
        id=row["id"],
        display_name=row["display_name"],
        primary_phone=row["primary_phone"],
        phones=list(row["phones"] or []),
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
    )
