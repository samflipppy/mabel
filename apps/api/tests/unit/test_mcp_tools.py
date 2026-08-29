from __future__ import annotations

from uuid import uuid4

from mabel.mcp.tokens import mint_tenant_token, parse_tenant_token
from mabel.mcp.tools import bind_tenant, call_tool, reset_store, reset_tenant, store


def setup_function() -> None:
    reset_store()


def test_write_tools_use_token_tenant_not_argument(monkeypatch) -> None:
    monkeypatch.setenv("MABEL_MCP_TOKEN_SECRET", "unit-test-token-secret")
    tenant_a = uuid4()
    tenant_b = uuid4()
    token = mint_tenant_token(tenant_a)
    parsed = parse_tenant_token(token)
    assert parsed.tenant_id == tenant_a

    bound = bind_tenant(parsed.tenant_id)
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
                "tenant_id": str(tenant_b),
            },
        )
    finally:
        reset_tenant(bound)

    leads = store().for_tenant(tenant_a)
    assert len(leads) == 1
    assert str(leads[0].id) == created["lead_id"]
    assert leads[0].dollars_won is None
    assert store().for_tenant(tenant_b) == []


def test_escalate_without_matching_rule_does_not_write() -> None:
    bound = bind_tenant(uuid4())
    try:
        result = call_tool(
            "escalate_emergency",
            {
                "vertical": "plumbing",
                "utterances": ["The kitchen sink is draining slow."],
                "captured": {"problem": "slow drain"},
            },
        )
    finally:
        reset_tenant(bound)
    assert result["escalated"] is False
    assert result["notify"] == "recap_7am"
    assert store().leads == []


def test_book_estimate_does_not_invent_arrival_time() -> None:
    bound = bind_tenant(uuid4())
    try:
        result = call_tool("book_estimate", {"arrival_time": "Tuesday at 8"})
    finally:
        reset_tenant(bound)
    assert result["booked"] is False
    assert "arrival time" in result["reason"]
