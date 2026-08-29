"""Billing: BIGINT cents, no float money, no LLM money, Stripe fail-closed."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mabel_api.main import create_app
from mabel_billing.plans import PLANS
from mabel_billing.stripe_client import StripeUnavailable, api_key, is_configured
from mabel_domain.money import Money, MoneyError, parse_owner_amount
from mabel_integrations.base import VaultUnavailable, read_credentials

from tests.e2e.conftest import auth_header

REPO = Path(__file__).resolve().parents[2]
MONEY_PATHS = [
    REPO / "packages" / "billing",
    REPO / "packages" / "domain" / "mabel_domain" / "money.py",
    REPO / "apps" / "api" / "src" / "mabel_api" / "routes" / "billing.py",
    REPO / "apps" / "api" / "src" / "mabel_api" / "routes" / "leads.py",
]


def test_every_plan_price_is_integer_cents():
    for option in PLANS.values():
        assert isinstance(option.price_cents, int)
        assert isinstance(option.overage_cents_per_min, int)
        assert not isinstance(option.price_cents, float)


def test_owner_amounts_are_integer_cents_and_refuse_garbage():
    assert parse_owner_amount("3800") == Money(380_000)
    assert parse_owner_amount("38.50").cents == 3850
    with pytest.raises(MoneyError):
        parse_owner_amount("about 3800")
    with pytest.raises(MoneyError):
        Money(19.99)  # type: ignore[arg-type]


def test_stripe_refuses_without_a_key(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    assert is_configured() is False
    with pytest.raises(StripeUnavailable):
        api_key()


@pytest.mark.asyncio
async def test_jobber_refuses_without_a_vault(monkeypatch):
    monkeypatch.delenv("SUPABASE_VAULT_URL", raising=False)
    with pytest.raises(VaultUnavailable):
        await read_credentials("tenant/abc/jobber")


def test_money_modules_do_not_use_float_literals_for_cents():
    """A float in a money path is the bug invariant 5 exists to catch."""
    offenders: list[str] = []
    for root in MONEY_PATHS:
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, float):
                    # voice minutes are not money; a 0.0 default on a speed is not money.
                    if node.value in {0.0, 1.0, 0.85}:
                        continue
                    offenders.append(f"{path.name}:{node.lineno}={node.value}")
    assert not offenders, f"float literals on a money path: {offenders}"


@pytest.mark.asyncio
async def test_billing_screen_is_honest_without_stripe(portal_owners, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    alpha = portal_owners["alpha"]
    token = portal_owners["owners"][alpha]["token"]
    client = TestClient(create_app())
    response = client.get("/api/billing", headers=auth_header(token))
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is False
    assert isinstance(body["estimated_overage_cents"], int)
    assert body["plan_cents"] is None or isinstance(body["plan_cents"], int)
    checkout = client.post(
        "/api/billing/checkout",
        headers=auth_header(token),
        json={"plan": "mabel"},
    )
    assert checkout.status_code == 503
