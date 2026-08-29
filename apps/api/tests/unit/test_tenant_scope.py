from __future__ import annotations

from uuid import uuid4

import pytest

from mabel.platform.db import MIGRATOR_ROLE, RoleError, _reject_migrator_url, tenant_scope


class FakeConn:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, query: str, params=None) -> None:
        self.queries.append(query)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_tenant_scope_sets_local_then_commits() -> None:
    conn = FakeConn()
    tenant_id = uuid4()
    with tenant_scope(tenant_id, conn) as scoped:
        assert scoped is conn
        scoped.execute("SELECT 1")
    assert conn.queries[0] == "BEGIN"
    assert conn.queries[1] == f"SET LOCAL app.tenant_id = '{tenant_id}'"
    assert conn.committed is True
    assert conn.rolled_back is False
    assert conn.closed is False


def test_tenant_scope_rolls_back_on_error() -> None:
    conn = FakeConn()
    tenant_id = uuid4()
    with pytest.raises(RuntimeError, match="boom"):
        with tenant_scope(tenant_id, conn):
            raise RuntimeError("boom")
    assert conn.queries[0] == "BEGIN"
    assert f"SET LOCAL app.tenant_id = '{tenant_id}'" in conn.queries[1]
    assert conn.rolled_back is True
    assert conn.committed is False


def test_tenant_scope_rejects_non_uuid() -> None:
    conn = FakeConn()
    with pytest.raises(ValueError, match="UUID"):
        with tenant_scope("not-a-tenant", conn):
            pass
    assert conn.queries == []


def test_app_will_not_connect_as_migrator() -> None:
    with pytest.raises(RoleError, match="migrator"):
        _reject_migrator_url(f"postgresql://{MIGRATOR_ROLE}:x@localhost/mabel")
    # The word BYPASSRLS does not appear in app connection code. The migrator
    # role holds it; this file never uses it.
