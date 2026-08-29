from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from mabel.billing import PLANS
from mabel.mcp.tools import bind_tenant, call_tool, reset_store, reset_tenant, store
from mabel.reports import sum_won


def setup_function() -> None:
    reset_store()


def test_no_lead_without_a_tenant() -> None:
    try:
        call_tool(
            "create_lead",
            {
                "name": "Pat Example",
                "address": "100 Example Ave, Lakewood OH 44107",
                "callback": "+12165550100",
                "problem": "slow drain",
                "urgency": "morning",
                "source": "google",
            },
        )
        raised = False
    except Exception:
        raised = True
    assert raised
    assert store().leads == []


def test_no_escalation_without_a_matching_rule() -> None:
    bound = bind_tenant(uuid4())
    try:
        result = call_tool(
            "escalate_emergency",
            {"vertical": "plumbing", "utterances": ["Just need a quote on a faucet."]},
        )
    finally:
        reset_tenant(bound)
    assert result["escalated"] is False
    assert all(lead.emergency_code is None for lead in store().leads)


def test_money_is_decimal_never_float() -> None:
    total = sum_won([Decimal("3800.00"), Decimal("199.50")])
    assert total == Decimal("3999.50")
    assert isinstance(total, Decimal)
    assert type(total) is Decimal
    for amount in PLANS.values():
        assert isinstance(amount, Decimal)
        assert type(amount) is Decimal
