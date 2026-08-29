from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from mabel.shops.packet import ShopPacket
from mabel.shops.store import replace_service_area_zips, update_shop_packet
from mabel.shops.update import update_shop
from mabel.sms.recap import RecapItem, queue_morning_recap
from mabel.sms.recap_store import RECAP_INSERT, persist_recap
from mabel.voice.archive import ARCHIVE_INSERT, archive_call, fetch_archives


class ScriptedConn:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.params: list = []
        self.zips: dict[str, str | None] = {}
        self.tenant_row = None
        self.did_row = None
        self.archives: list[tuple] = []
        self.recaps: list[tuple] = []
        self._tenant: str | None = None
        self.committed = False
        self.rolled_back = False

    def execute(self, query: str, params=None):
        self.queries.append(query)
        self.params.append(params)
        stripped = query.strip()
        lowered = " ".join(query.split()).lower()
        if stripped == "BEGIN":
            self._tenant = None
            return _Rows([])
        if stripped.startswith("SET LOCAL app.tenant_id"):
            self._tenant = stripped.split("'")[1]
            return _Rows([])
        if "select id from tenants" in lowered:
            if self._tenant is None:
                return _Rows([])
            return _Rows([(self._tenant,)])
        if "update tenants set" in lowered:
            return _Rows([])
        if "update service_area_zips set retired_at = now()" in lowered:
            if self._tenant is None:
                return _Rows([])
            for zip_code in list(self.zips):
                self.zips[zip_code] = "now"
            return _Rows([])
        if "update service_area_zips set retired_at = null" in lowered:
            zip_code = params[0] if params else None
            if zip_code in self.zips:
                self.zips[zip_code] = None
            return _Rows([])
        if "select zip from service_area_zips where zip" in lowered:
            zip_code = params[0] if params else None
            if zip_code in self.zips:
                return _Rows([(zip_code,)])
            return _Rows([])
        if "insert into service_area_zips" in lowered:
            zip_code = params[1]
            self.zips[zip_code] = None
            return _Rows([])
        if "from service_area_zips" in lowered and "retired_at is null" in lowered:
            if self._tenant is None:
                return _Rows([])
            active = [
                (zip_code,)
                for zip_code, retired in sorted(self.zips.items())
                if retired is None
            ]
            return _Rows(active)
        if "from tenants" in lowered:
            if self._tenant is None:
                return _Rows([])
            return _Rows([self.tenant_row] if self.tenant_row is not None else [])
        if "from inbound_dids" in lowered:
            if self._tenant is None:
                return _Rows([])
            return _Rows([self.did_row] if self.did_row is not None else [])
        if "insert into call_archives" in lowered:
            self.archives.append(tuple(params))
            return _Rows([])
        if "from call_archives" in lowered:
            if self._tenant is None:
                return _Rows([])
            visible = [row for row in self.archives if str(row[1]) == self._tenant]
            return _Rows(visible)
        if "insert into recap_queue" in lowered:
            self.recaps.append(tuple(params))
            return _Rows([])
        if "from app.due_recap_tenants" in lowered:
            tenants = {str(row[1]) for row in self.recaps}
            return _Rows([(tenant,) for tenant in tenants])
        if "from recap_queue" in lowered and "sent_at is null" in lowered:
            due = [row for row in self.recaps if len(row) < 5 or row[4] is None]
            return _Rows(due)
        if "update recap_queue set sent_at" in lowered:
            recap_id = params[1] if params else None
            when = params[0] if params else None
            for index, row in enumerate(self.recaps):
                if str(row[0]) == str(recap_id):
                    self.recaps[index] = (row[0], row[1], row[2], row[3], when)
            return _Rows([])
        return _Rows([])

    def commit(self) -> None:
        self.committed = True
        self._tenant = None

    def rollback(self) -> None:
        self.rolled_back = True
        self._tenant = None

    def close(self) -> None:
        return None


class _Rows:
    def __init__(self, rows) -> None:
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def test_zip_replace_retires_old_and_inserts_new() -> None:
    from mabel.platform.db import tenant_scope

    conn = ScriptedConn()
    tenant_id = uuid4()
    conn.zips = {"44107": None}
    with tenant_scope(tenant_id, conn):
        replace_service_area_zips(conn, tenant_id, ("44114",))
    joined = "\n".join(conn.queries)
    assert "UPDATE service_area_zips SET retired_at = now()" in joined
    assert "INSERT INTO service_area_zips" in joined
    assert "DELETE" not in joined.upper()
    assert conn.zips.get("44107") == "now"
    assert conn.zips.get("44114") is None


def test_update_shop_packet_sets_local_and_does_not_flip_live() -> None:
    from mabel.platform.db import tenant_scope

    conn = ScriptedConn()
    tenant_id = uuid4()
    packet = ShopPacket(
        tenant_id=tenant_id,
        name="Renamed Plumbing",
        vertical="plumbing",
        owner_sms_e164="+12165550111",
        service_area_zips=("44107",),
        greeting_notes="Ask how the dog is.",
    )
    with tenant_scope(tenant_id, conn):
        update_shop_packet(conn, packet, replace_zips=False)
    joined = "\n".join(conn.queries)
    assert "UPDATE tenants SET" in joined
    assert "live" not in joined.lower() or "live" not in str(conn.params)
    assert "BYPASSRLS" not in joined.upper()
    assert "mabel_migrator" not in joined


def test_archive_insert_sets_local_and_isolates_tenants() -> None:
    conn = ScriptedConn()
    tenant_a = uuid4()
    tenant_b = uuid4()
    row_a = archive_call(
        tenant_id=tenant_a,
        call_id="call-a",
        transcript="Slow drain at the example house.",
        conn=conn,
    )
    archive_call(
        tenant_id=tenant_b,
        call_id="call-b",
        transcript="Burst pipe at the other house.",
        conn=conn,
    )
    seen_a = fetch_archives(tenant_a, conn)
    seen_b = fetch_archives(tenant_b, conn)
    assert [item.id for item in seen_a] == [row_a.id]
    assert all(item.tenant_id == tenant_a for item in seen_a)
    assert all(item.tenant_id == tenant_b for item in seen_b)
    joined = "\n".join(conn.queries)
    assert "SET LOCAL app.tenant_id" in joined
    assert "dollars_won" not in ARCHIVE_INSERT
    assert "float" not in joined.lower()
    assert "BYPASSRLS" not in joined.upper()


def test_recap_insert_sets_local_and_leaves_sent_at_null() -> None:
    conn = ScriptedConn()
    tenant = uuid4()
    item = RecapItem(tenant_id=tenant, recap_at=datetime.now(timezone.utc))
    persist_recap(item, conn)
    joined = "\n".join(conn.queries)
    assert conn.queries[0] == "BEGIN"
    assert f"SET LOCAL app.tenant_id = '{tenant}'" in joined
    assert "INSERT INTO recap_queue" in joined
    assert "sent_at" not in RECAP_INSERT
    insert_params = next(
        params
        for query, params in zip(conn.queries, conn.params)
        if "INSERT INTO recap_queue" in query
    )
    assert str(item.id) in insert_params
    assert str(tenant) in insert_params
    assert conn.committed is True
    assert "BYPASSRLS" not in joined.upper()


def test_mark_recap_sent_sets_local_and_does_not_delete() -> None:
    from mabel.sms.recap_store import RECAP_MARK_SENT, mark_recap_sent

    conn = ScriptedConn()
    tenant = uuid4()
    item = RecapItem(
        tenant_id=tenant,
        recap_at=datetime.now(timezone.utc),
        sent_at=datetime.now(timezone.utc),
    )
    mark_recap_sent(item, conn)
    joined = "\n".join(conn.queries)
    assert conn.queries[0] == "BEGIN"
    assert f"SET LOCAL app.tenant_id = '{tenant}'" in joined
    assert "UPDATE recap_queue SET sent_at" in joined
    assert "DELETE" not in joined.upper()
    assert "sent_at" in RECAP_MARK_SENT
    assert conn.committed is True
    assert "BYPASSRLS" not in joined.upper()


def test_queue_morning_recap_persists_when_database_url_set(monkeypatch) -> None:
    recorded: list[RecapItem] = []

    def fake_persist(item: RecapItem, conn=None) -> None:
        recorded.append(item)

    monkeypatch.setenv("DATABASE_URL", "postgresql://mabel_app@localhost/mabel")
    monkeypatch.setattr("mabel.sms.recap.packet_for", lambda tenant_id: None)
    monkeypatch.setattr("mabel.sms.recap_store.persist_recap", fake_persist)
    tenant = uuid4()
    item = queue_morning_recap(tenant)
    assert recorded == [item]
    assert item.tenant_id == tenant
    assert item.recap_at.hour == 7


def test_update_shop_memory_rejects_dollar_notes(monkeypatch) -> None:
    from mabel.platform.tenancy import reset_directory
    from mabel.shops.onboard import onboard_shop
    from mabel.shops.packet import PacketError, reset_packets

    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_directory()
    reset_packets()
    shop = onboard_shop(
        name="Example Plumbing",
        vertical="plumbing",
        inbound_did="+12165550199",
        owner_sms_e164="+12165550111",
        service_area_zips=("44107",),
    )
    with pytest.raises(PacketError, match="dollar"):
        update_shop(shop.tenant_id, greeting_notes="Trip fee is $99", greeting_notes_set=True)
