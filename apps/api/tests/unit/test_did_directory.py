from __future__ import annotations

from datetime import time
from uuid import uuid4

import pytest

from mabel.platform.tenancy import (
    MemoryDidDirectory,
    PostgresDidDirectory,
    Tenant,
    UnknownDidError,
    directory,
    reset_directory,
)
from mabel.shops.packet import PacketError
from mabel.shops.store import fetch_shop_packet


class ScriptedConn:
    def __init__(
        self,
        *,
        tenant_id=None,
        tenant_row=None,
        zip_rows=None,
        did_row=None,
    ) -> None:
        self.tenant_id = tenant_id
        self.tenant_row = tenant_row
        self.zip_rows = zip_rows or []
        self.did_row = did_row
        self.queries: list[str] = []
        self.params: list = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, query: str, params=None):
        self.queries.append(query)
        self.params.append(params)
        lowered = " ".join(query.split()).lower()
        if "resolve_tenant_from_did" in lowered:
            return _Rows(self.did_row)
        if "from tenants" in lowered:
            return _Rows([self.tenant_row] if self.tenant_row is not None else [])
        if "from service_area_zips" in lowered:
            return _Rows(self.zip_rows)
        return _Rows([])

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


class _Rows:
    def __init__(self, rows) -> None:
        if rows is None:
            self._rows = []
        elif rows and not isinstance(rows[0], (list, tuple)):
            self._rows = [rows]
        else:
            self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def setup_function() -> None:
    reset_directory()


def test_memory_directory_stays_without_database_url(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_directory()
    found = directory()
    assert isinstance(found, MemoryDidDirectory)
    tenant = Tenant(id=uuid4(), vertical="plumbing", name="Example Plumbing")
    found.register("+12165550199", tenant)
    assert directory().resolve("+12165550199").id == tenant.id


def test_memory_directory_fails_closed_on_unknown_did() -> None:
    with pytest.raises(UnknownDidError, match="does not know this number"):
        MemoryDidDirectory().resolve("+12165550999")


def test_database_url_selects_postgres_directory(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://mabel_app@localhost/mabel")
    assert isinstance(directory(), PostgresDidDirectory)


def test_postgres_directory_uses_resolve_function_then_tenant_scope() -> None:
    tenant_id = uuid4()
    conn = ScriptedConn(
        tenant_id=tenant_id,
        did_row=(tenant_id,),
        tenant_row=(
            tenant_id,
            "Example Plumbing",
            "plumbing",
            "America/New_York",
            "+12165550199",
            time(17, 0),
            time(8, 0),
            "Ask how the dog is.",
        ),
        zip_rows=[("44107",)],
    )
    tenant = PostgresDidDirectory(conn).resolve("+12165550199")
    joined = " ".join(conn.queries)
    assert "app.resolve_tenant_from_did" in joined
    assert f"SET LOCAL app.tenant_id = '{tenant_id}'" in joined
    assert "FROM tenants" in joined
    assert "FROM service_area_zips" in joined
    assert tenant.id == tenant_id
    assert tenant.vertical == "plumbing"
    assert tenant.packet is not None
    assert tenant.packet.service_area_zips == ("44107",)
    assert tenant.packet.greeting_notes == "Ask how the dog is."
    assert conn.committed is True


def test_postgres_directory_fails_closed_on_unknown_did() -> None:
    conn = ScriptedConn(did_row=None)
    with pytest.raises(UnknownDidError, match="does not know this number"):
        PostgresDidDirectory(conn).resolve("+12165550999")
    assert any("resolve_tenant_from_did" in q for q in conn.queries)


def test_fetch_shop_packet_rejects_dollar_greeting_notes() -> None:
    tenant_id = uuid4()
    conn = ScriptedConn(
        tenant_row=(
            tenant_id,
            "Example Plumbing",
            "plumbing",
            "America/New_York",
            "+12165550199",
            time(17, 0),
            time(8, 0),
            "Tell them it's $99.",
        ),
        zip_rows=[("44107",)],
    )
    with pytest.raises(PacketError, match="dollar"):
        fetch_shop_packet(conn, tenant_id)
