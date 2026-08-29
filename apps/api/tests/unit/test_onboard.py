from __future__ import annotations

from datetime import time
from uuid import UUID, uuid4

import pytest

from mabel.mcp.tools import TOOL_NAMES, bind_tenant, call_tool, reset_store, reset_tenant
from mabel.platform.tenancy import (
    DuplicateDidError,
    MemoryDidDirectory,
    UnknownDidError,
    directory,
    reset_directory,
)
from mabel.shops.onboard import OnboardedShop, onboard_shop
from mabel.shops.packet import PacketError, ShopPacket, reset_packets
from mabel.shops.store import SHOP_STATUS_DRAFT, persist_onboarded_shop
from mabel.voice.webhook import AGENT_LIVE


def setup_function() -> None:
    reset_store()
    reset_packets()
    reset_directory()


def _onboard(**overrides) -> OnboardedShop:
    values = {
        "name": "Example Plumbing",
        "vertical": "plumbing",
        "inbound_did": "+12165550199",
        "owner_sms_e164": "+12165550111",
        "service_area_zips": ("44107",),
    }
    values.update(overrides)
    return onboard_shop(**values)


def test_onboard_two_shops_did_a_resolves_to_a_not_b() -> None:
    shop_a = _onboard(
        name="Shop A",
        inbound_did="+12165550101",
        owner_sms_e164="+12165550111",
        service_area_zips=("44107",),
    )
    shop_b = _onboard(
        name="Shop B",
        inbound_did="+12165550102",
        owner_sms_e164="+12165550112",
        service_area_zips=("44102",),
    )

    resolved_a = directory().resolve("+12165550101")
    resolved_b = directory().resolve("+12165550102")

    assert shop_a.tenant_id != shop_b.tenant_id
    assert resolved_a.id == shop_a.tenant_id
    assert resolved_b.id == shop_b.tenant_id
    assert resolved_a.id != shop_b.tenant_id
    assert resolved_a.name == "Shop A"
    assert resolved_b.name == "Shop B"
    with pytest.raises(UnknownDidError):
        directory().resolve("+12165550999")


def test_onboard_zip_isolation_still_holds() -> None:
    shop_a = _onboard(
        name="Shop A",
        inbound_did="+12165550101",
        service_area_zips=("44107",),
    )
    shop_b = _onboard(
        name="Shop B",
        inbound_did="+12165550102",
        owner_sms_e164="+12165550112",
        service_area_zips=("44102",),
    )

    bound_a = bind_tenant(shop_a.tenant_id)
    try:
        in_a = call_tool("get_service_area", {"zip_code": "44107"})
        out_a = call_tool("get_service_area", {"zip_code": "44102"})
    finally:
        reset_tenant(bound_a)

    bound_b = bind_tenant(shop_b.tenant_id)
    try:
        in_b = call_tool("get_service_area", {"zip_code": "44102"})
        out_b = call_tool("get_service_area", {"zip_code": "44107"})
    finally:
        reset_tenant(bound_b)

    assert in_a == {"zip": "44107", "in_area": True}
    assert out_a == {"zip": "44102", "in_area": False}
    assert in_b == {"zip": "44102", "in_area": True}
    assert out_b == {"zip": "44107", "in_area": False}


def test_onboard_rejects_dollar_greeting() -> None:
    with pytest.raises(PacketError, match="dollar"):
        _onboard(greeting_notes="Tell them it's $99 after hours.")


def test_onboard_rejects_duplicate_did() -> None:
    _onboard(inbound_did="+12165550199")
    with pytest.raises(DuplicateDidError, match="already answers this number"):
        _onboard(
            name="Other Shop",
            inbound_did="+12165550199",
            owner_sms_e164="+12165550122",
            service_area_zips=("44102",),
        )


def test_onboard_status_is_draft_and_agent_stays_not_live() -> None:
    shop = _onboard()
    assert shop.status == SHOP_STATUS_DRAFT
    assert shop.status == "draft"
    assert shop.status != "live"
    assert directory().resolve(shop.inbound_did).status == "draft"
    assert AGENT_LIVE is False


def test_onboard_defaults_timezone_and_writes_memory_directory(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_directory()
    shop = _onboard(timezone="")
    assert shop.packet.timezone == "America/New_York"
    assert shop.packet.after_hours_start == time(17, 0)
    assert shop.packet.after_hours_end == time(8, 0)
    assert isinstance(directory(), MemoryDidDirectory)
    assert directory().resolve(shop.inbound_did).packet is shop.packet


def test_onboard_is_not_an_mcp_tool() -> None:
    assert "onboard_shop" not in TOOL_NAMES
    assert "create_shop" not in TOOL_NAMES


def test_onboard_mints_tenant_id_and_ignores_none_from_caller() -> None:
    shop = _onboard()
    assert isinstance(shop.tenant_id, UUID)
    assert shop.tenant_id == shop.packet.tenant_id


def test_onboard_rejects_bad_inbound_did() -> None:
    with pytest.raises(PacketError, match="inbound number"):
        _onboard(inbound_did="not-a-phone")


class ScriptedConn:
    def __init__(self, *, did_row=None, fail_on_insert=None) -> None:
        self.did_row = did_row
        self.fail_on_insert = fail_on_insert
        self.queries: list[str] = []
        self.params: list = []
        self.committed = False
        self.rolled_back = False

    def execute(self, query: str, params=None):
        self.queries.append(query)
        self.params.append(params)
        lowered = " ".join(query.split()).lower()
        if "resolve_tenant_from_did" in lowered:
            return _Rows(self.did_row)
        if self.fail_on_insert is not None and "insert into inbound_dids" in lowered:
            raise self.fail_on_insert
        return _Rows([])

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        return None


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


def test_postgres_onboard_sets_local_then_inserts_draft() -> None:
    conn = ScriptedConn(did_row=None)
    shop = _onboard(
        name="DB Shop",
        inbound_did="+12165550301",
        service_area_zips=("44107", "44102"),
        conn=conn,
    )
    joined = "\n".join(conn.queries)
    assert conn.queries[0] == "BEGIN"
    assert conn.queries[1] == f"SET LOCAL app.tenant_id = '{shop.tenant_id}'"
    assert "SELECT app.resolve_tenant_from_did" in joined
    assert "INSERT INTO tenants" in joined
    assert "INSERT INTO inbound_dids" in joined
    assert "INSERT INTO service_area_zips" in joined
    assert "BYPASSRLS" not in joined.upper()
    assert "mabel_migrator" not in joined
    tenant_insert_params = next(
        params for query, params in zip(conn.queries, conn.params) if "INSERT INTO tenants" in query
    )
    assert str(shop.tenant_id) in tenant_insert_params
    assert SHOP_STATUS_DRAFT in tenant_insert_params
    assert "live" not in tenant_insert_params
    assert shop.status == "draft"
    assert conn.committed is True
    assert conn.rolled_back is False


def test_postgres_onboard_duplicate_did_fails_closed() -> None:
    conn = ScriptedConn(did_row=(uuid4(),))
    with pytest.raises(DuplicateDidError, match="already answers this number"):
        _onboard(inbound_did="+12165550302", conn=conn)
    assert conn.rolled_back is True
    assert conn.committed is False
    assert not any("INSERT INTO tenants" in query for query in conn.queries)


def test_postgres_onboard_unique_violation_fails_closed() -> None:
    class UniqueError(Exception):
        sqlstate = "23505"

    conn = ScriptedConn(did_row=None, fail_on_insert=UniqueError("duplicate key"))
    packet = ShopPacket(
        tenant_id=uuid4(),
        name="DB Shop",
        vertical="plumbing",
        owner_sms_e164="+12165550111",
        service_area_zips=("44107",),
    )
    with pytest.raises(DuplicateDidError, match="already answers this number"):
        persist_onboarded_shop(conn, packet, "+12165550399")
