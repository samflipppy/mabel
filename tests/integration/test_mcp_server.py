"""The MCP endpoint, over HTTP, with no database.

`tools/call` needs a transaction, so those tests live in `tests/isolation/`.
What is here is the protocol surface and the authorisation boundary — the parts
that must behave correctly before any tenant data is involved.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mabel_mcp.server import PROTOCOL_VERSION, router
from mabel_mcp.tokens import mint_call_token

KEY = "a-test-signing-key-long-enough-to-be-accepted"
TENANT = UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def client(monkeypatch):
    # The server reads the key from the environment, as it does in production.
    # Setting a test key here is not a stubbed credential: it signs nothing
    # that leaves the process.
    monkeypatch.setenv("MCP_TOKEN_SIGNING_KEY", KEY)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_call_token(TENANT, 'call_abc', key=KEY)}"}


def rpc(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    body: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


class TestAuthorisation:
    def test_no_header_is_refused(self, client):
        response = client.post("/mcp", json=rpc("tools/list"))
        assert response.status_code == 401

    def test_a_forged_token_is_refused(self, client):
        forged = mint_call_token(TENANT, "call_abc", key="a-completely-different-key-32-chars!")
        response = client.post(
            "/mcp", json=rpc("tools/list"), headers={"Authorization": f"Bearer {forged}"}
        )
        assert response.status_code == 401

    def test_a_non_bearer_header_is_refused(self, client):
        response = client.post(
            "/mcp", json=rpc("tools/list"), headers={"Authorization": "Basic abc"}
        )
        assert response.status_code == 401

    def test_tools_list_is_not_an_authentication_exception(self, client):
        """The tool list is the same for every tenant, which is exactly the
        argument that would make it an exception. Making it one is how an
        authentication exception ends up somewhere it matters."""
        assert client.post("/mcp", json=rpc("tools/list")).status_code == 401

    def test_the_refusal_says_nothing_about_why(self, client):
        # An unauthenticated caller learning whether a token was expired or
        # forged is a caller learning something.
        response = client.post(
            "/mcp", json=rpc("tools/list"), headers={"Authorization": "Bearer nonsense"}
        )
        body = response.json()
        assert body["error"]["message"] == "unauthorized"
        assert "expired" not in str(body).lower()
        assert "signature" not in str(body).lower()


class TestProtocol:
    def test_initialize(self, client):
        response = client.post("/mcp", json=rpc("initialize"), headers=auth())
        result = response.json()["result"]
        assert result["protocolVersion"] == PROTOCOL_VERSION
        assert result["serverInfo"]["name"] == "mabel"
        assert "tools" in result["capabilities"]

    def test_tools_list_returns_the_nine(self, client):
        response = client.post("/mcp", json=rpc("tools/list"), headers=auth())
        tools = response.json()["result"]["tools"]
        assert [t["name"] for t in tools] == [
            "lookup_customer",
            "get_service_area",
            "check_availability",
            "create_lead",
            "escalate_emergency",
            "book_estimate",
            "get_job_history",
            "answer_question",
            "log_note",
        ]

    def test_every_listed_tool_carries_a_schema(self, client):
        tools = client.post("/mcp", json=rpc("tools/list"), headers=auth()).json()["result"][
            "tools"
        ]
        for tool in tools:
            assert tool["inputSchema"]["type"] == "object"
            assert tool["description"]

    def test_no_listed_tool_takes_a_tenant(self, client):
        """Over the wire, not just in the module. This is what xAI actually
        sees, and it is the last place to catch a tool that would let the model
        choose whose data it reads."""
        tools = client.post("/mcp", json=rpc("tools/list"), headers=auth()).json()["result"][
            "tools"
        ]
        for tool in tools:
            for name in tool["inputSchema"].get("properties", {}):
                assert "tenant" not in name.lower()
                assert "account" not in name.lower()

    def test_the_request_id_comes_back(self, client):
        response = client.post("/mcp", json=rpc("tools/list", request_id=77), headers=auth())
        assert response.json()["id"] == 77

    def test_an_unknown_method_is_a_jsonrpc_error_not_a_404(self, client):
        response = client.post("/mcp", json=rpc("tools/destroy"), headers=auth())
        assert response.status_code == 200
        assert response.json()["error"]["code"] == -32601

    def test_a_malformed_body_is_a_parse_error(self, client):
        response = client.post(
            "/mcp", content=b"not json", headers={**auth(), "Content-Type": "application/json"}
        )
        assert response.json()["error"]["code"] == -32700

    def test_a_batch_request_is_refused_explicitly(self, client):
        """Legal JSON-RPC, and we do not accept it: a batch spanning two tool
        calls needs two transactions, and doing that in one request means an
        ambiguous partial failure."""
        response = client.post("/mcp", json=[rpc("tools/list")], headers=auth())
        assert response.json()["error"]["code"] == -32600

    def test_tools_call_without_a_name_is_invalid_params(self, client):
        response = client.post("/mcp", json=rpc("tools/call", {}), headers=auth())
        assert response.json()["error"]["code"] == -32602

    def test_tools_call_with_non_object_arguments_is_invalid_params(self, client):
        response = client.post(
            "/mcp",
            json=rpc("tools/call", {"name": "log_note", "arguments": "a string"}),
            headers=auth(),
        )
        assert response.json()["error"]["code"] == -32602

    def test_ping_is_answered(self, client):
        assert client.post("/mcp", json=rpc("ping"), headers=auth()).json()["result"] == {}


class TestHealth:
    def test_it_needs_no_token(self, client):
        assert client.get("/mcp/health").status_code == 200

    def test_it_reports_the_tool_count(self, client):
        # Enough to catch a deploy that shipped a truncated tool list.
        assert client.get("/mcp/health").json() == {"status": "ok", "tools": 9}

    def test_it_says_nothing_about_any_tenant(self, client):
        body = client.get("/mcp/health").text
        assert "tenant" not in body.lower()
