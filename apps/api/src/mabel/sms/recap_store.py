"""Recap queue SQL. Runs inside tenant_scope except the due-tenant bootstrap.

Listing which shops have a due recap is the same problem as DID resolve: the
tenant is not known yet. `app.due_recap_tenants` is a read-only definer
function. Each shop's rows are then read and updated under SET LOCAL
app.tenant_id. No DELETE. Application code still never uses the migrator role.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from mabel.platform.config import load_settings
from mabel.platform.db import connect, tenant_scope
from mabel.sms.recap import RecapItem

RECAP_INSERT = """
        INSERT INTO recap_queue (id, tenant_id, recap_at, lead_id)
        VALUES (%s, %s, %s, %s)
        """

RECAP_SELECT = """
        SELECT id, tenant_id, recap_at, lead_id, sent_at
        FROM recap_queue
        """

RECAP_DUE_SELECT = """
        SELECT id, tenant_id, recap_at, lead_id, sent_at
        FROM recap_queue
        WHERE sent_at IS NULL AND recap_at <= %s
        """

RECAP_MARK_SENT = """
        UPDATE recap_queue SET sent_at = %s WHERE id = %s AND sent_at IS NULL
        """

DUE_RECAP_TENANTS = "SELECT tenant_id FROM app.due_recap_tenants(%s)"


def using_database() -> bool:
    return bool(load_settings().database_url)


def insert_recap(conn: Any, item: RecapItem) -> None:
    """INSERT one recap row. Caller already SET LOCAL app.tenant_id. sent_at stays null."""
    conn.execute(
        RECAP_INSERT,
        (
            str(item.id),
            str(item.tenant_id),
            item.recap_at,
            None if item.lead_id is None else str(item.lead_id),
        ),
    )


def persist_recap(item: RecapItem, conn: Any | None = None) -> None:
    with tenant_scope(item.tenant_id, conn) as scoped:
        insert_recap(scoped, item)


def fetch_due_recap_tenants(now: datetime, conn: Any | None = None) -> list[UUID]:
    """Bootstrap. Tenant is not known yet, so this uses app.due_recap_tenants."""
    owns = conn is None
    if owns:
        conn = connect()
    try:
        rows = conn.execute(DUE_RECAP_TENANTS, (now,)).fetchall()
    finally:
        if owns:
            conn.close()
    found: list[UUID] = []
    for row in rows:
        if row and row[0] is not None:
            found.append(UUID(str(row[0])))
    return found


def list_due_recaps(conn: Any, now: datetime) -> list[RecapItem]:
    """SELECT due rows visible under the current SET LOCAL."""
    rows = conn.execute(RECAP_DUE_SELECT, (now,)).fetchall()
    return [_item_from_row(row) for row in rows]


def load_due_recaps(now: datetime, conn: Any | None = None) -> list[RecapItem]:
    items: list[RecapItem] = []
    for tenant_id in fetch_due_recap_tenants(now, conn):
        with tenant_scope(tenant_id, conn) as scoped:
            items.extend(list_due_recaps(scoped, now))
    return items


def mark_recap_sent(item: RecapItem, conn: Any | None = None) -> None:
    """Set sent_at. Does not delete the queue row."""
    when = item.sent_at
    if when is None:
        when = datetime.now(timezone.utc)
    with tenant_scope(item.tenant_id, conn) as scoped:
        scoped.execute(RECAP_MARK_SENT, (when, str(item.id)))


def _item_from_row(row: Any) -> RecapItem:
    recap_at = row[2]
    if isinstance(recap_at, str):
        recap_at = datetime.fromisoformat(recap_at)
    if isinstance(recap_at, datetime) and recap_at.tzinfo is None:
        recap_at = recap_at.replace(tzinfo=timezone.utc)
    sent_at = row[4] if len(row) > 4 else None
    if isinstance(sent_at, str):
        sent_at = datetime.fromisoformat(sent_at)
    if isinstance(sent_at, datetime) and sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=timezone.utc)
    lead_raw = row[3]
    return RecapItem(
        id=UUID(str(row[0])),
        tenant_id=UUID(str(row[1])),
        recap_at=recap_at,
        lead_id=None if lead_raw is None else UUID(str(lead_raw)),
        sent_at=sent_at,
    )
