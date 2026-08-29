"""Recap queue SQL. Runs inside tenant_scope. This PR does not send the 7am text."""

from __future__ import annotations

from typing import Any

from mabel.platform.config import load_settings
from mabel.platform.db import tenant_scope
from mabel.sms.recap import RecapItem

RECAP_INSERT = """
        INSERT INTO recap_queue (id, tenant_id, recap_at, lead_id)
        VALUES (%s, %s, %s, %s)
        """

RECAP_SELECT = """
        SELECT id, tenant_id, recap_at, lead_id, sent_at
        FROM recap_queue
        """


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
