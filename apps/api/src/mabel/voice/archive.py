"""Archive transcript and a recording placeholder on our side.

xAI's cache is not storage. The call ends, we copy it, done.
Memory when DATABASE_URL is unset. SQL through tenant_scope otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from mabel.platform.config import load_settings
from mabel.platform.db import tenant_scope

ARCHIVE_INSERT = """
        INSERT INTO call_archives (
            id, tenant_id, call_id, transcript, recording_uri
        )
        VALUES (%s, %s, %s, %s, %s)
        """

ARCHIVE_SELECT = """
        SELECT id, tenant_id, call_id, transcript, recording_uri
        FROM call_archives
        """


@dataclass(frozen=True)
class CallArchive:
    tenant_id: UUID
    call_id: str
    transcript: str
    recording_uri: str
    id: UUID = field(default_factory=uuid4)


_archives: list[CallArchive] = []


def reset_archives() -> None:
    _archives.clear()


def memory_archives() -> list[CallArchive]:
    return list(_archives)


def using_database() -> bool:
    return bool(load_settings().database_url)


def recording_placeholder(*, tenant_id: UUID, call_id: str) -> str:
    return f"placeholder:mabel-archive/{tenant_id}/{call_id}"


def insert_archive(conn: Any, row: CallArchive) -> None:
    """INSERT one archive. Caller already SET LOCAL app.tenant_id."""
    conn.execute(
        ARCHIVE_INSERT,
        (
            str(row.id),
            str(row.tenant_id),
            row.call_id,
            row.transcript,
            row.recording_uri,
        ),
    )


def persist_archive(row: CallArchive, conn: Any | None = None) -> None:
    with tenant_scope(row.tenant_id, conn) as scoped:
        insert_archive(scoped, row)


def list_archives(conn: Any) -> list[CallArchive]:
    rows = conn.execute(ARCHIVE_SELECT).fetchall()
    return [_from_row(row) for row in rows]


def fetch_archives(tenant_id: UUID, conn: Any | None = None) -> list[CallArchive]:
    if using_database() or conn is not None:
        with tenant_scope(tenant_id, conn) as scoped:
            return list_archives(scoped)
    return [row for row in _archives if row.tenant_id == tenant_id]


def archive_call(
    *,
    tenant_id: UUID,
    call_id: str,
    transcript: str,
    recording_uri: str | None = None,
    conn: Any | None = None,
) -> CallArchive:
    uri = recording_uri or recording_placeholder(tenant_id=tenant_id, call_id=call_id)
    row = CallArchive(
        tenant_id=tenant_id,
        call_id=call_id,
        transcript=transcript,
        recording_uri=uri,
    )
    if conn is not None or using_database():
        persist_archive(row, conn)
        return row
    _archives.append(row)
    return row


def _from_row(row: Any) -> CallArchive:
    return CallArchive(
        id=UUID(str(row[0])),
        tenant_id=UUID(str(row[1])),
        call_id=str(row[2]),
        transcript=str(row[3]),
        recording_uri=str(row[4]),
    )
