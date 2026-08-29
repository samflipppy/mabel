from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from mabel.leads.models import Lead, Note
from mabel.leads.persist import (
    LEAD_INSERT,
    NOTE_INSERT,
    fetch_leads,
    fetch_notes,
    persist_lead,
    persist_note,
)
from mabel.mcp.tools import bind_tenant, call_tool, reset_store, reset_tenant, store
from mabel.platform.config import ConfigError
from mabel.platform.tenancy import reset_directory
from mabel.shops.auth import (
    BAD_ADMIN_TOKEN,
    AdminAuthError,
    verify_admin_authorization,
)
from mabel.shops.onboard import onboard_shop
from mabel.shops.packet import reset_packets


def setup_function() -> None:
    reset_store()
    reset_packets()
    reset_directory()


def _lead(tenant_id, **overrides) -> Lead:
    values = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "name": "Pat Example",
        "address": "100 Example Ave, Lakewood OH 44107",
        "callback": "+12165550100",
        "problem": "slow drain",
        "urgency": "morning is fine",
        "source": "google",
    }
    values.update(overrides)
    return Lead(**values)


class RlsConn:
    """Stand-in for Postgres RLS: unset tenant sees zero rows."""

    def __init__(self) -> None:
        self.leads: list[tuple] = []
        self.notes: list[tuple] = []
        self.queries: list[str] = []
        self.params: list = []
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
        if "insert into leads" in lowered:
            self.leads.append(tuple(params))
            return _Rows([])
        if "update leads set sms_sent" in lowered:
            return _Rows([])
        if "insert into notes" in lowered:
            self.notes.append(tuple(params))
            return _Rows([])
        if "from leads" in lowered:
            if self._tenant is None:
                return _Rows([])
            visible = []
            for row in self.leads:
                if str(row[1]) == self._tenant:
                    created = datetime.now(timezone.utc)
                    sms_sent = row[9] if len(row) > 9 else None
                    sms_reason = row[10] if len(row) > 10 else None
                    visible.append(tuple(row[:9]) + (None, created, sms_sent, sms_reason))
            return _Rows(visible)
        if "from notes" in lowered:
            if self._tenant is None:
                return _Rows([])
            visible = [row for row in self.notes if str(row[1]) == self._tenant]
            return _Rows(visible)
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


def test_onboard_shop_python_works_without_admin_token(monkeypatch) -> None:
    monkeypatch.delenv("MABEL_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    shop = onboard_shop(
        name="Example Plumbing",
        vertical="plumbing",
        inbound_did="+12165550199",
        owner_sms_e164="+12165550111",
        service_area_zips=("44107",),
    )
    assert shop.status == "draft"
    assert shop.inbound_did == "+12165550199"


def test_verify_admin_missing_config_is_config_error(monkeypatch) -> None:
    monkeypatch.delenv("MABEL_ADMIN_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="MABEL_ADMIN_TOKEN"):
        verify_admin_authorization("Bearer anything")


def test_verify_admin_wrong_token_is_auth_error(monkeypatch) -> None:
    monkeypatch.setenv("MABEL_ADMIN_TOKEN", "correct-token")
    with pytest.raises(AdminAuthError, match=BAD_ADMIN_TOKEN):
        verify_admin_authorization("Bearer wrong-token")


def test_verify_admin_right_token_passes(monkeypatch) -> None:
    monkeypatch.setenv("MABEL_ADMIN_TOKEN", "correct-token")
    verify_admin_authorization("Bearer correct-token")


def test_create_lead_memory_does_not_set_dollars_won(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    tenant = uuid4()
    bound = bind_tenant(tenant)
    try:
        created = call_tool(
            "create_lead",
            {
                "name": "Pat Example",
                "address": "100 Example Ave, Lakewood OH 44107",
                "callback": "+12165550100",
                "problem": "slow drain",
                "urgency": "morning is fine",
                "source": "google",
                "dollars_won": "3800.00",
            },
        )
    finally:
        reset_tenant(bound)
    leads = store().for_tenant(tenant)
    assert len(leads) == 1
    assert str(leads[0].id) == created["lead_id"]
    assert leads[0].dollars_won is None
    assert "dollars_won" not in created


def test_create_lead_uses_postgres_when_database_url_set(monkeypatch) -> None:
    recorded: list[Lead] = []

    def fake_persist(lead: Lead, conn=None) -> None:
        recorded.append(lead)

    monkeypatch.setenv("DATABASE_URL", "postgresql://mabel_app@localhost/mabel")
    monkeypatch.setattr("mabel.mcp.tools.persist_lead", fake_persist)
    tenant = uuid4()
    bound = bind_tenant(tenant)
    try:
        created = call_tool(
            "create_lead",
            {
                "name": "Pat Example",
                "address": "100 Example Ave, Lakewood OH 44107",
                "callback": "+12165550100",
                "problem": "burst pipe",
                "urgency": "now",
                "source": "google",
            },
        )
    finally:
        reset_tenant(bound)
    assert store().leads == []
    assert len(recorded) == 1
    assert str(recorded[0].id) == created["lead_id"]
    assert recorded[0].tenant_id == tenant
    assert recorded[0].dollars_won is None


def test_log_note_uses_postgres_when_database_url_set(monkeypatch) -> None:
    recorded: list[Note] = []

    def fake_persist(note: Note, conn=None) -> None:
        recorded.append(note)

    monkeypatch.setenv("DATABASE_URL", "postgresql://mabel_app@localhost/mabel")
    monkeypatch.setattr("mabel.mcp.tools.persist_note", fake_persist)
    tenant = uuid4()
    bound = bind_tenant(tenant)
    try:
        created = call_tool("log_note", {"body": "Caller will be home after 6."})
    finally:
        reset_tenant(bound)
    assert store().notes == []
    assert len(recorded) == 1
    assert str(recorded[0].id) == created["note_id"]
    assert recorded[0].tenant_id == tenant


def test_escalate_emergency_writes_lead_without_dollars_won() -> None:
    tenant = uuid4()
    bound = bind_tenant(tenant)
    try:
        result = call_tool(
            "escalate_emergency",
            {
                "vertical": "plumbing",
                "utterances": ["The pipe burst and water is everywhere."],
                "captured": {
                    "name": "Pat Example",
                    "address": "100 Example Ave",
                    "callback": "+12165550100",
                    "problem": "burst pipe",
                    "urgency": "now",
                    "source": "google",
                },
                "context": {"after_hours": True},
            },
        )
    finally:
        reset_tenant(bound)
    assert result["escalated"] is True
    leads = store().for_tenant(tenant)
    assert len(leads) == 1
    assert leads[0].dollars_won is None
    assert leads[0].emergency_code == result["trigger"]


def test_postgres_lead_insert_sets_local_and_omits_dollars_won() -> None:
    conn = RlsConn()
    tenant = uuid4()
    lead = _lead(tenant)
    persist_lead(lead, conn)
    joined = "\n".join(conn.queries)
    assert conn.queries[0] == "BEGIN"
    assert conn.queries[1] == f"SET LOCAL app.tenant_id = '{tenant}'"
    assert "INSERT INTO leads" in joined
    assert "dollars_won" not in LEAD_INSERT
    insert_params = next(
        params for query, params in zip(conn.queries, conn.params) if "INSERT INTO leads" in query
    )
    assert len(insert_params) == 11
    assert str(lead.id) in insert_params
    assert str(tenant) in insert_params
    assert "3800.00" not in insert_params
    assert insert_params[8] is None  # emergency_code; dollars_won is not a bound param
    assert conn.committed is True
    assert "BYPASSRLS" not in joined.upper()
    assert "mabel_migrator" not in joined
    assert "float" not in joined.lower()


def test_postgres_note_insert_sets_local() -> None:
    conn = RlsConn()
    tenant = uuid4()
    note = Note(id=uuid4(), tenant_id=tenant, body="Called back, waiting on parts.")
    persist_note(note, conn)
    joined = "\n".join(conn.queries)
    assert conn.queries[0] == "BEGIN"
    assert conn.queries[1] == f"SET LOCAL app.tenant_id = '{tenant}'"
    assert "INSERT INTO notes" in joined
    assert "dollars_won" not in NOTE_INSERT
    assert conn.committed is True


def test_tenant_a_cannot_read_tenant_b_lead() -> None:
    conn = RlsConn()
    tenant_a = uuid4()
    tenant_b = uuid4()
    lead_a = _lead(tenant_a, name="Caller A", problem="slow drain")
    lead_b = _lead(tenant_b, name="Caller B", problem="no heat")
    persist_lead(lead_a, conn)
    persist_lead(lead_b, conn)

    seen_a = fetch_leads(tenant_a, conn)
    seen_b = fetch_leads(tenant_b, conn)

    assert [lead.id for lead in seen_a] == [lead_a.id]
    assert [lead.id for lead in seen_b] == [lead_b.id]
    assert all(lead.tenant_id == tenant_a for lead in seen_a)
    assert all(lead.tenant_id == tenant_b for lead in seen_b)
    assert lead_b.id not in {lead.id for lead in seen_a}
    assert lead_a.id not in {lead.id for lead in seen_b}
    assert all(lead.dollars_won is None for lead in seen_a + seen_b)


def test_tenant_a_cannot_read_tenant_b_note() -> None:
    conn = RlsConn()
    tenant_a = uuid4()
    tenant_b = uuid4()
    note_a = Note(id=uuid4(), tenant_id=tenant_a, body="Note for A")
    note_b = Note(id=uuid4(), tenant_id=tenant_b, body="Note for B")
    persist_note(note_a, conn)
    persist_note(note_b, conn)
    seen_a = fetch_notes(tenant_a, conn)
    seen_b = fetch_notes(tenant_b, conn)
    assert [note.id for note in seen_a] == [note_a.id]
    assert [note.id for note in seen_b] == [note_b.id]


def test_lead_select_without_set_local_returns_nothing() -> None:
    conn = RlsConn()
    tenant = uuid4()
    persist_lead(_lead(tenant), conn)
    # No SET LOCAL: fail-safe deny.
    assert conn.execute("SELECT id FROM leads").fetchall() == []


def test_auth_module_uses_compare_digest_and_does_not_log() -> None:
    from mabel.shops import auth

    source = Path(auth.__file__).read_text(encoding="utf-8")
    assert "hmac.compare_digest" in source
    assert "print(" not in source
    assert "logger" not in source.lower()
    assert "logging" not in source.lower()
