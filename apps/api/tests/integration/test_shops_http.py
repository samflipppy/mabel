from __future__ import annotations

from fastapi.testclient import TestClient

from mabel.app import create_app
from mabel.mcp.tools import TOOL_NAMES, reset_store
from mabel.platform.tenancy import directory, reset_directory
from mabel.shops.packet import reset_packets
from mabel.voice.webhook import AGENT_LIVE


def _client() -> TestClient:
    reset_store()
    reset_packets()
    reset_directory()
    return TestClient(create_app())


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


def test_post_shops_creates_draft_and_does_not_echo_secrets() -> None:
    client = _client()
    response = client.post("/shops", json=_body())
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "draft"
    assert data["live"] is False
    assert data["draft"] is True
    assert data["name"] == "Example Plumbing"
    assert data["inbound_did"] == "+12165550199"
    assert data["service_area_zips"] == ["44107"]
    assert data["timezone"] == "America/New_York"
    assert AGENT_LIVE is False
    assert "sk-" not in response.text
    assert "whsec_" not in response.text
    assert "api_key" not in response.text.lower()
    assert "DATABASE_URL" not in response.text
    assert "TELNYX" not in response.text
    assert "XAI_API_KEY" not in response.text
    tenant = directory().resolve(data["inbound_did"])
    assert str(tenant.id) == data["tenant_id"]
    assert tenant.status == "draft"


def test_post_shops_two_dids_stay_isolated() -> None:
    client = _client()
    shop_a = client.post(
        "/shops",
        json=_body(
            name="Shop A",
            inbound_did="+12165550101",
            service_area_zips=["44107"],
        ),
    )
    shop_b = client.post(
        "/shops",
        json=_body(
            name="Shop B",
            inbound_did="+12165550102",
            owner_sms_e164="+12165550112",
            service_area_zips=["44102"],
        ),
    )
    assert shop_a.status_code == 201
    assert shop_b.status_code == 201
    id_a = shop_a.json()["tenant_id"]
    id_b = shop_b.json()["tenant_id"]
    assert id_a != id_b
    assert str(directory().resolve("+12165550101").id) == id_a
    assert str(directory().resolve("+12165550102").id) == id_b
    assert str(directory().resolve("+12165550101").id) != id_b


def test_post_shops_rejects_dollar_greeting() -> None:
    client = _client()
    response = client.post(
        "/shops",
        json=_body(greeting_notes="After-hours rate is 89.00"),
    )
    assert response.status_code == 400
    assert "dollar" in response.json()["detail"]


def test_post_shops_rejects_duplicate_did() -> None:
    client = _client()
    first = client.post("/shops", json=_body())
    assert first.status_code == 201
    second = client.post(
        "/shops",
        json=_body(name="Other Shop", owner_sms_e164="+12165550122"),
    )
    assert second.status_code == 409
    assert "already answers this number" in second.json()["detail"]


def test_post_shops_rejects_client_tenant_id() -> None:
    client = _client()
    body = _body()
    body["tenant_id"] = "00000000-0000-0000-0000-000000000001"
    response = client.post("/shops", json=body)
    assert response.status_code == 422

    query = client.post(
        "/shops?tenant_id=00000000-0000-0000-0000-000000000001",
        json=_body(inbound_did="+12165550401"),
    )
    assert query.status_code == 400
    assert "tenant_id" in query.json()["detail"]


def test_onboard_is_not_mounted_as_mcp() -> None:
    assert "onboard_shop" not in TOOL_NAMES
    client = _client()
    # MCP still requires a token; missing token is 401, not an onboard route.
    listed = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed.status_code == 401
