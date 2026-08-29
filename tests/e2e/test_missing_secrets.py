"""Missing Telnyx / xAI / Fly / Stripe secrets: the app starts, the live
path refuses, nothing crashes, nothing leaks.
"""

from __future__ import annotations

import os

from fastapi.testclient import TestClient
from mabel_api.main import create_app
from mabel_media.inbound import accept_inbound_call
from mabel_telnyx.client import delivery_risk
from mabel_xai.client import FORBIDDEN_MODEL_ALIAS, VOICE_MODEL

from tests.e2e.fakes import XAI_WEBHOOK_SECRET, inbound_payload, sign_xai


def test_the_app_starts_with_no_vendor_secrets(monkeypatch):
    for name in (
        "TELNYX_API_KEY",
        "TELNYX_PUBLIC_KEY",
        "TELNYX_10DLC_CAMPAIGN_ID",
        "XAI_API_KEY",
        "XAI_WEBHOOK_SECRET",
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "FLY_API_TOKEN",
        "FLY_API_KEY",
        "DATABASE_URL",
        "SUPABASE_JWT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_is_degraded_without_a_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app())
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def test_inbound_refuses_without_a_webhook_secret(monkeypatch):
    monkeypatch.delenv("XAI_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("TELNYX_API_KEY", raising=False)

    client = TestClient(create_app())
    response = client.post("/webhooks/xai/inbound", content=b"{}", headers={})
    assert response.status_code == 503
    body = response.json()
    assert body["accepted"] is False
    assert body["joined"] is False
    assert "not configured" in body["reason"]


def test_inbound_refuses_an_unsigned_body_without_crashing(monkeypatch):
    monkeypatch.setenv("XAI_WEBHOOK_SECRET", XAI_WEBHOOK_SECRET)
    client = TestClient(create_app())
    response = client.post("/webhooks/xai/inbound", content=inbound_payload())
    assert response.status_code == 401
    assert response.json()["accepted"] is False


async def test_missing_telnyx_or_xai_refuses_after_tenant_would_resolve(monkeypatch):
    """Keys are checked on the live path. The process stays up."""
    monkeypatch.setenv("XAI_WEBHOOK_SECRET", XAI_WEBHOOK_SECRET)
    monkeypatch.delenv("TELNYX_API_KEY", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    body = inbound_payload()
    decision = await accept_inbound_call(body, sign_xai(body), engine=None)
    assert decision.status_code == 503
    assert decision.handed_off is False
    assert decision.body["joined"] is False


def test_delivery_risk_is_honest_about_10dlc():
    """Unregistered 10DLC is the dangerous state. Surface it; do not hide it."""
    # This process has no campaign id in the environment on purpose.
    os.environ.pop("TELNYX_10DLC_CAMPAIGN_ID", None)
    if os.environ.get("TELNYX_API_KEY"):
        assert delivery_risk() == "unregistered"
    else:
        assert delivery_risk() == "no_key"


def test_the_voice_model_is_pinned_and_the_alias_is_named_so_a_grep_finds_it():
    assert VOICE_MODEL == "grok-voice-think-fast-2.0"
    assert FORBIDDEN_MODEL_ALIAS == "grok-voice-latest"


def test_no_response_body_echoes_a_secret_value(monkeypatch):
    fake = "sk-this-is-not-a-real-xai-key-do-not-leak"
    monkeypatch.setenv("XAI_WEBHOOK_SECRET", XAI_WEBHOOK_SECRET)
    monkeypatch.setenv("XAI_API_KEY", fake)
    client = TestClient(create_app())
    response = client.post("/webhooks/xai/inbound", content=inbound_payload())
    blob = response.text
    assert fake not in blob
    assert XAI_WEBHOOK_SECRET not in blob
