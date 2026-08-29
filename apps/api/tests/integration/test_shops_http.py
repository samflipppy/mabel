from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from mabel.app import create_app
from mabel.mcp.tools import TOOL_NAMES, reset_store
from mabel.platform.tenancy import directory, reset_directory
from mabel.shops import http as shops_http
from mabel.shops.packet import reset_packets
from mabel.voice.agents import FakeXaiAgentsClient, bind_voice_agent_client
from mabel.voice.webhook import AGENT_LIVE

ADMIN_TOKEN = "unit-test-admin-token"


def _client(monkeypatch, *, admin_token: str | None = ADMIN_TOKEN) -> TestClient:
    reset_store()
    reset_packets()
    reset_directory()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    if admin_token is None:
        monkeypatch.delenv("MABEL_ADMIN_TOKEN", raising=False)
    else:
        monkeypatch.setenv("MABEL_ADMIN_TOKEN", admin_token)
    return TestClient(create_app())


def _auth(token: str = ADMIN_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _body(**overrides) -> dict:
    payload = {
        "name": "Example Plumbing",
        "vertical": "plumbing",
        "inbound_did": "+12165550199",
        "owner_sms_e164": "+12165550111",
        "service_area_zips": ["44107"],
    }
    payload.update(overrides)
    return payload


def test_post_shops_without_admin_token_configured_is_503(monkeypatch) -> None:
    client = _client(monkeypatch, admin_token=None)
    response = client.post("/shops", json=_body(), headers=_auth("any-token"))
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "MABEL_ADMIN_TOKEN" in detail
    assert "any-token" not in response.text
    assert "sk-" not in response.text


def test_post_shops_wrong_token_is_401(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.post("/shops", json=_body(), headers=_auth("wrong-admin-token"))
    assert response.status_code == 401
    assert "wrong-admin-token" not in response.text
    assert ADMIN_TOKEN not in response.text
    assert "does not accept this token" in response.json()["detail"]


def test_post_shops_missing_bearer_is_401(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.post("/shops", json=_body())
    assert response.status_code == 401
    assert ADMIN_TOKEN not in response.text


def test_post_shops_right_token_creates_draft_and_does_not_echo_secrets(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.post("/shops", json=_body(), headers=_auth())
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "draft"
    assert data["live"] is False
    assert data["draft"] is True
    assert data["name"] == "Example Plumbing"
    assert data["inbound_did"] == "+12165550199"
    assert data["service_area_zips"] == ["44107"]
    assert data["timezone"] == "America/New_York"
    assert data["xai_voice_agent_id"] is None
    assert AGENT_LIVE is False
    assert "sk-" not in response.text
    assert "whsec_" not in response.text
    assert "api_key" not in response.text.lower()
    assert "DATABASE_URL" not in response.text
    assert "TELNYX" not in response.text
    assert "XAI_API_KEY" not in response.text
    assert ADMIN_TOKEN not in response.text
    tenant = directory().resolve(data["inbound_did"])
    assert str(tenant.id) == data["tenant_id"]
    assert tenant.status == "draft"


def test_post_shops_two_dids_stay_isolated(monkeypatch) -> None:
    client = _client(monkeypatch)
    shop_a = client.post(
        "/shops",
        json=_body(
            name="Shop A",
            inbound_did="+12165550101",
            service_area_zips=["44107"],
        ),
        headers=_auth(),
    )
    shop_b = client.post(
        "/shops",
        json=_body(
            name="Shop B",
            inbound_did="+12165550102",
            owner_sms_e164="+12165550112",
            service_area_zips=["44102"],
        ),
        headers=_auth(),
    )
    assert shop_a.status_code == 201
    assert shop_b.status_code == 201
    id_a = shop_a.json()["tenant_id"]
    id_b = shop_b.json()["tenant_id"]
    assert id_a != id_b
    assert str(directory().resolve("+12165550101").id) == id_a
    assert str(directory().resolve("+12165550102").id) == id_b
    assert str(directory().resolve("+12165550101").id) != id_b


def test_post_shops_rejects_dollar_greeting(monkeypatch) -> None:
    client = _client(monkeypatch)
    response = client.post(
        "/shops",
        json=_body(greeting_notes="After-hours rate is 89.00"),
        headers=_auth(),
    )
    assert response.status_code == 400
    assert "dollar" in response.json()["detail"]


def test_post_shops_rejects_duplicate_did(monkeypatch) -> None:
    client = _client(monkeypatch)
    first = client.post("/shops", json=_body(), headers=_auth())
    assert first.status_code == 201
    second = client.post(
        "/shops",
        json=_body(name="Other Shop", owner_sms_e164="+12165550122"),
        headers=_auth(),
    )
    assert second.status_code == 409
    assert "already answers this number" in second.json()["detail"]


def test_post_shops_creates_agent_when_xai_key_set(monkeypatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("MABEL_MCP_PUBLIC_URL", "https://mabel.fly.dev/mcp")
    bind_voice_agent_client(FakeXaiAgentsClient(next_id="agent_http"))
    client = _client(monkeypatch)
    response = client.post("/shops", json=_body(inbound_did="+12165550177"), headers=_auth())
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "draft"
    assert data["live"] is False
    assert data["xai_voice_agent_id"] == "agent_http"
    assert AGENT_LIVE is False


def test_post_shops_rejects_client_xai_voice_agent_id(monkeypatch) -> None:
    client = _client(monkeypatch)
    body = _body()
    body["xai_voice_agent_id"] = "agent_from_client"
    response = client.post("/shops", json=body, headers=_auth())
    assert response.status_code == 422


def test_post_shops_rejects_client_tenant_id(monkeypatch) -> None:
    client = _client(monkeypatch)
    body = _body()
    body["tenant_id"] = "00000000-0000-0000-0000-000000000001"
    response = client.post("/shops", json=body, headers=_auth())
    assert response.status_code == 422

    query = client.post(
        "/shops?tenant_id=00000000-0000-0000-0000-000000000001",
        json=_body(inbound_did="+12165550401"),
        headers=_auth(),
    )
    assert query.status_code == 400
    assert "tenant_id" in query.json()["detail"]


def test_onboard_is_not_mounted_as_mcp(monkeypatch) -> None:
    assert "onboard_shop" not in TOOL_NAMES
    client = _client(monkeypatch)
    # MCP still requires a token; missing token is 401, not an onboard route.
    listed = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed.status_code == 401


def test_shops_http_does_not_log_the_token() -> None:
    source = Path(shops_http.__file__).read_text(encoding="utf-8")
    lowered = source.lower()
    assert "print(" not in source
    assert "logger" not in lowered
    assert "logging" not in lowered
    assert "hmac.compare_digest" not in source
    # Auth comparison lives in shops.auth; the route only reads the header name.
    assert "authorization" in lowered


def test_patch_replaces_zip_list_and_keeps_other_tenant(monkeypatch) -> None:
    client = _client(monkeypatch)
    shop_a = client.post(
        "/shops",
        json=_body(name="Shop A", inbound_did="+12165550101", service_area_zips=["44107"]),
        headers=_auth(),
    )
    shop_b = client.post(
        "/shops",
        json=_body(
            name="Shop B",
            inbound_did="+12165550102",
            owner_sms_e164="+12165550112",
            service_area_zips=["44102"],
        ),
        headers=_auth(),
    )
    id_a = shop_a.json()["tenant_id"]
    id_b = shop_b.json()["tenant_id"]
    patched = client.patch(
        f"/shops/{id_a}",
        json={"service_area_zips": ["44114", "44107"]},
        headers=_auth(),
    )
    assert patched.status_code == 200
    assert patched.json()["service_area_zips"] == ["44114", "44107"]
    assert patched.json()["live"] is False
    assert AGENT_LIVE is False
    got_a = client.get(f"/shops/{id_a}", headers=_auth())
    got_b = client.get(f"/shops/{id_b}", headers=_auth())
    assert got_a.json()["service_area_zips"] == ["44114", "44107"]
    assert got_b.json()["service_area_zips"] == ["44102"]
    assert got_b.json()["name"] == "Shop B"
    assert id_a not in got_b.text or got_b.json()["tenant_id"] == id_b


def test_patch_dollar_greeting_notes_is_400(monkeypatch) -> None:
    client = _client(monkeypatch)
    created = client.post("/shops", json=_body(inbound_did="+12165550133"), headers=_auth())
    tenant_id = created.json()["tenant_id"]
    response = client.patch(
        f"/shops/{tenant_id}",
        json={"greeting_notes": "After-hours rate is 89.00"},
        headers=_auth(),
    )
    assert response.status_code == 400
    assert "dollar" in response.json()["detail"]


def test_get_other_tenant_does_not_leak_this_shop(monkeypatch) -> None:
    client = _client(monkeypatch)
    shop_a = client.post(
        "/shops",
        json=_body(name="Shop A", inbound_did="+12165550101", service_area_zips=["44107"]),
        headers=_auth(),
    )
    missing = client.get(
        "/shops/00000000-0000-0000-0000-000000000099",
        headers=_auth(),
    )
    assert missing.status_code == 404
    assert shop_a.json()["name"] not in missing.text


def test_overnight_empty_when_no_leads(monkeypatch) -> None:
    client = _client(monkeypatch)
    created = client.post("/shops", json=_body(inbound_did="+12165550144"), headers=_auth())
    tenant_id = created.json()["tenant_id"]
    response = client.get(f"/shops/{tenant_id}/overnight", headers=_auth())
    assert response.status_code == 200
    data = response.json()
    assert data["leads"] == []
    assert data["shop_name"] == "Example Plumbing"


def test_overnight_shows_captured_lead_not_invented(monkeypatch) -> None:
    from mabel.mcp.tools import bind_tenant, call_tool, reset_tenant

    client = _client(monkeypatch)
    created = client.post("/shops", json=_body(inbound_did="+12165550155"), headers=_auth())
    tenant_id = created.json()["tenant_id"]
    bound = bind_tenant(UUID(tenant_id))
    try:
        call_tool(
            "create_lead",
            {
                "name": "Pat Example",
                "address": "100 Example Ave, Lakewood OH 44107",
                "callback": "+12165550100",
                "problem": "slow drain",
                "urgency": "morning is fine",
                "source": "google",
            },
        )
    finally:
        reset_tenant(bound)
    response = client.get(f"/shops/{tenant_id}/overnight", headers=_auth())
    assert response.status_code == 200
    leads = response.json()["leads"]
    assert len(leads) == 1
    assert leads[0]["name"] == "Pat Example"
    assert leads[0]["problem"] == "slow drain"
    assert leads[0]["emergency"] is False
    assert leads[0]["sms_sent"] is False
    assert "time" in leads[0]
    other = client.post(
        "/shops",
        json=_body(
            name="Other Shop",
            inbound_did="+12165550156",
            owner_sms_e164="+12165550122",
            service_area_zips=["44102"],
        ),
        headers=_auth(),
    )
    other_id = other.json()["tenant_id"]
    empty = client.get(f"/shops/{other_id}/overnight", headers=_auth())
    assert empty.json()["leads"] == []


def test_patch_does_not_accept_vertical_or_live(monkeypatch) -> None:
    client = _client(monkeypatch)
    created = client.post("/shops", json=_body(inbound_did="+12165550166"), headers=_auth())
    tenant_id = created.json()["tenant_id"]
    response = client.patch(
        f"/shops/{tenant_id}",
        json={"vertical": "electrical", "name": "Still Plumbing"},
        headers=_auth(),
    )
    assert response.status_code == 422
    live = client.patch(
        f"/shops/{tenant_id}",
        json={"live": True},
        headers=_auth(),
    )
    assert live.status_code == 422
