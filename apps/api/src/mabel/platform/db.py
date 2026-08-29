"""Postgres access for Mabel.

The app connects as a non-superuser role with no BYPASSRLS. Every query goes
through tenant_scope(), which opens a transaction and SET LOCAL app.tenant_id
before anything runs. If that setting is missing, RLS matches zero rows.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol
from uuid import UUID

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

APP_ROLE = "mabel_app"
MIGRATOR_ROLE = "mabel_migrator"


class TenantConnection(Protocol):
    def execute(self, query: str, params: Any | None = None) -> Any: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


class RoleError(RuntimeError):
    """The app tried to connect as a role it is not allowed to use."""


def _reject_migrator_url(database_url: str) -> None:
    # Never connect as the migrator. That role holds BYPASSRLS.
    lowered = database_url.lower()
    if f"{MIGRATOR_ROLE}:" in lowered or f"user={MIGRATOR_ROLE}" in lowered:
        raise RoleError("Mabel will not connect as the migrator role.")


def connect(database_url: str | None = None) -> Any:
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Mabel is missing DATABASE_URL.")
    _reject_migrator_url(url)
    import psycopg

    conn = psycopg.connect(url, autocommit=False)
    return conn


@contextmanager
def tenant_scope(
    tenant_id: UUID | str,
    conn: Any | None = None,
    *,
    database_url: str | None = None,
) -> Iterator[Any]:
    """BEGIN, SET LOCAL app.tenant_id, yield, then commit or rollback.

    Pass an existing connection (tests do this). Otherwise a new app-role
    connection is opened and closed.
    """
    owns_connection = conn is None
    if owns_connection:
        conn = connect(database_url)
    tenant = str(tenant_id)
    if not _UUID_RE.fullmatch(tenant):
        raise ValueError("tenant_id must be a UUID.")
    try:
        conn.execute("BEGIN")
        # UUID-validated literal. SET LOCAL does not take bound parameters.
        conn.execute(f"SET LOCAL app.tenant_id = '{tenant}'")
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        if owns_connection:
            conn.close()
