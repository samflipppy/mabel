from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from mabel.app import create_app
from mabel.mcp.tokens import mint_tenant_token
from mabel.mcp.tools import reset_store, store


def test_mcp_streamable_http_tools_are_tenant_scoped(monkeypatch) -> None:
    reset_store()
    monkeypatch.setenv("MABEL_MCP_TOKEN_SECRET", "integration-token-secret")
    tenant_a = uuid4()
    tenant_b = uuid4()
    client = TestClient(create_app())

    listed = client.post(
        "/mcp/",
        headers={"Authorization": f"Bearer {mint_tenant_token(tenant_a)}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert listed.status_code == 200
    names = [tool["name"] for tool in listed.json()["result"]["tools"]]
    assert names == [
        "lookup_customer",
        "get_service_area",
        "check_availability",
        "create_lead",
        "escalate_emergency",
        "book_estimate",
        "get_job_history",
        "log_note",
    ]

    created = client.post(
        "/mcp/",
        headers={"Authorization": f"Bearer {mint_tenant_token(tenant_a)}"},
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "create_lead",
                "arguments": {
                    "name": "Pat Example",
                    "address": "100 Example Ave, Lakewood OH 44107",
                    "callback": "+12165550100",
                    "problem": "slow drain",
                    "urgency": "morning",
                    "source": "google",
                    "tenant_id": str(tenant_b),
                },
            },
        },
    )
    assert created.status_code == 200
    assert created.json()["result"]["structuredContent"]["created"] is True
    assert store().for_tenant(tenant_a)
    assert store().for_tenant(tenant_b) == []


def test_mcp_rejects_missing_token(monkeypatch) -> None:
    monkeypatch.setenv("MABEL_MCP_TOKEN_SECRET", "integration-token-secret")
    client = TestClient(create_app())
    response = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code == 401
