"""Lead and note SQL. Runs inside tenant_scope. RLS matches on app.tenant_id.

dollars_won stays null on write. The model must not write it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from mabel.leads.models import Lead, Note
from mabel.platform.config import load_settings
from mabel.platform.db import tenant_scope

LEAD_INSERT = """
        INSERT INTO leads (
            id, tenant_id, name, address, callback, problem, urgency, source,
            emergency_code, sms_sent, sms_reason
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

LEAD_SMS_UPDATE = """
        UPDATE leads SET sms_sent = %s, sms_reason = %s WHERE id = %s
        """

NOTE_INSERT = """
        INSERT INTO notes (id, tenant_id, body)
        VALUES (%s, %s, %s)
        """

LEAD_SELECT = """
        SELECT id, tenant_id, name, address, callback, problem,
               urgency, source, emergency_code, dollars_won, created_at,
               sms_sent, sms_reason
        FROM leads
        """

NOTE_SELECT = """
        SELECT id, tenant_id, body
        FROM notes
        """


def using_database() -> bool:
    return bool(load_settings().database_url)


def insert_lead(conn: Any, lead: Lead) -> None:
    """INSERT one lead. Caller already SET LOCAL app.tenant_id. No dollars_won column."""
    conn.execute(
        LEAD_INSERT,
        (
            str(lead.id),
            str(lead.tenant_id),
            lead.name,
            lead.address,
            lead.callback,
            lead.problem,
            lead.urgency,
            lead.source,
            lead.emergency_code,
            lead.sms_sent,
            lead.sms_reason,
        ),
    )


def insert_note(conn: Any, note: Note) -> None:
    """INSERT one note. Caller already SET LOCAL app.tenant_id."""
    conn.execute(
        NOTE_INSERT,
        (str(note.id), str(note.tenant_id), note.body),
    )


def persist_lead(lead: Lead, conn: Any | None = None) -> None:
    with tenant_scope(lead.tenant_id, conn) as scoped:
        insert_lead(scoped, lead)


def persist_note(note: Note, conn: Any | None = None) -> None:
    with tenant_scope(note.tenant_id, conn) as scoped:
        insert_note(scoped, note)


def update_lead_sms(lead: Lead, conn: Any | None = None) -> None:
    """Record whether the owner text went out. Does not touch dollars_won."""
    if using_database() or conn is not None:
        with tenant_scope(lead.tenant_id, conn) as scoped:
            scoped.execute(
                LEAD_SMS_UPDATE,
                (lead.sms_sent, lead.sms_reason, str(lead.id)),
            )


def list_leads(conn: Any) -> list[Lead]:
    """SELECT leads visible under the current SET LOCAL. RLS hides other tenants."""
    rows = conn.execute(LEAD_SELECT).fetchall()
    return [_lead_from_row(row) for row in rows]


def list_notes(conn: Any) -> list[Note]:
    rows = conn.execute(NOTE_SELECT).fetchall()
    return [_note_from_row(row) for row in rows]


def fetch_leads(tenant_id: UUID, conn: Any | None = None) -> list[Lead]:
    with tenant_scope(tenant_id, conn) as scoped:
        return list_leads(scoped)


def fetch_notes(tenant_id: UUID, conn: Any | None = None) -> list[Note]:
    with tenant_scope(tenant_id, conn) as scoped:
        return list_notes(scoped)


def _lead_from_row(row: Any) -> Lead:
    created = None
    sms_sent = None
    sms_reason = None
    if len(row) > 10 and row[10] is not None:
        created = row[10] if isinstance(row[10], datetime) else datetime.fromisoformat(str(row[10]))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
    if len(row) > 11:
        sms_sent = None if row[11] is None else bool(row[11])
    if len(row) > 12:
        sms_reason = None if row[12] is None else str(row[12])
    kwargs: dict[str, Any] = {}
    if created is not None:
        kwargs["created_at"] = created
    return Lead(
        id=UUID(str(row[0])),
        tenant_id=UUID(str(row[1])),
        name=str(row[2]),
        address=str(row[3]),
        callback=str(row[4]),
        problem=str(row[5]),
        urgency=str(row[6]),
        source=str(row[7]),
        emergency_code=None if row[8] is None else str(row[8]),
        dollars_won=_as_money(row[9]) if len(row) > 9 else None,
        sms_sent=sms_sent,
        sms_reason=sms_reason,
        **kwargs,
    )


def _note_from_row(row: Any) -> Note:
    return Note(id=UUID(str(row[0])), tenant_id=UUID(str(row[1])), body=str(row[2]))


def _as_money(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, float):
        raise TypeError("Mabel does not read money from a float.")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
