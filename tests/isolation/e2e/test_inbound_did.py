"""To-DID → tenant, before any socket. Unknown DID fail-closed."""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from mabel_api.main import create_app
from mabel_media.inbound import accept_inbound_call, bind_inbound_opener
from mabel_xai.client import VOICE_MODEL

from tests.e2e.fakes import (
    NOW,
    XAI_WEBHOOK_SECRET,
    RecordingOpener,
    inbound_payload,
    sign_xai,
)

pytestmark = pytest.mark.asyncio

ALPHA_DID = "+12165550148"
UNKNOWN_DID = "+12165550000"


@pytest.fixture
def inbound_env(monkeypatch, bind_db):
    monkeypatch.setenv("XAI_WEBHOOK_SECRET", XAI_WEBHOOK_SECRET)
    monkeypatch.setenv("TELNYX_API_KEY", "KEY_NOT_A_REAL_TELNYX_KEY")
    monkeypatch.setenv("XAI_API_KEY", "KEY_NOT_A_REAL_XAI_KEY")
    return bind_db


class TestTenantFromTheDialedNumber:
    async def test_a_known_did_resolves_before_any_socket(
        self, inbound_env, two_tenants: tuple[UUID, UUID]
    ):
        alpha, _beta = two_tenants
        opener = RecordingOpener()
        body = inbound_payload(to_did=ALPHA_DID, call_id="call_known")
        decision = await accept_inbound_call(
            body, sign_xai(body, webhook_id="msg_known"), engine=inbound_env, opener=opener
        )
        assert decision.status_code == 200
        assert decision.body["accepted"] is True
        assert decision.body["tenant_resolved"] is True
        assert decision.body["joined"] is True
        assert decision.body["voice_model"] == VOICE_MODEL
        assert decision.body["live"] is False
        assert len(opener.calls) == 1
        assert opener.calls[0].tenant_id == alpha
        assert opener.calls[0].to_e164 == ALPHA_DID
        assert opener.calls[0].call_id == "call_known"

    async def test_a_sip_to_header_resolves_the_same_way(
        self, inbound_env, two_tenants: tuple[UUID, UUID]
    ):
        alpha, _beta = two_tenants
        opener = RecordingOpener()
        body = inbound_payload(to_did=ALPHA_DID, call_id="call_sip", sip=True)
        decision = await accept_inbound_call(
            body, sign_xai(body, webhook_id="msg_sip"), engine=inbound_env, opener=opener
        )
        assert decision.status_code == 200
        assert opener.calls[0].tenant_id == alpha
        assert opener.calls[0].to_e164 == ALPHA_DID

    async def test_an_unknown_did_never_hands_off(
        self, inbound_env, two_tenants: tuple[UUID, UUID]
    ):
        del two_tenants
        opener = RecordingOpener()
        body = inbound_payload(to_did=UNKNOWN_DID, call_id="call_unknown")
        decision = await accept_inbound_call(
            body, sign_xai(body, webhook_id="msg_unknown"), engine=inbound_env, opener=opener
        )
        assert decision.status_code == 404
        assert decision.body["tenant_resolved"] is False
        assert decision.body["joined"] is False
        assert decision.handed_off is False
        assert opener.calls == []

    async def test_a_tenant_id_in_the_payload_is_ignored(
        self, inbound_env, two_tenants: tuple[UUID, UUID]
    ):
        """The model — or a forged webhook body — cannot choose the tenant."""
        alpha, beta = two_tenants
        opener = RecordingOpener()
        import json

        payload = json.loads(inbound_payload(to_did=ALPHA_DID, call_id="call_smuggle"))
        payload["tenant_id"] = str(beta)
        payload["data"] = {"tenant_id": str(beta)}
        body = json.dumps(payload, separators=(",", ":")).encode()
        decision = await accept_inbound_call(
            body, sign_xai(body, webhook_id="msg_smuggle"), engine=inbound_env, opener=opener
        )
        assert decision.status_code == 200
        assert opener.calls[0].tenant_id == alpha
        assert opener.calls[0].tenant_id != beta


class TestMissingKeysAfterResolve:
    async def test_known_did_without_telnyx_refuses_and_does_not_join(
        self, monkeypatch, bind_db, two_tenants: tuple[UUID, UUID]
    ):
        del two_tenants
        monkeypatch.setenv("XAI_WEBHOOK_SECRET", XAI_WEBHOOK_SECRET)
        monkeypatch.setenv("XAI_API_KEY", "KEY_NOT_A_REAL_XAI_KEY")
        monkeypatch.delenv("TELNYX_API_KEY", raising=False)
        opener = RecordingOpener()
        body = inbound_payload(call_id="call_no_telnyx")
        decision = await accept_inbound_call(
            body, sign_xai(body, webhook_id="msg_no_telnyx"), engine=bind_db, opener=opener
        )
        assert decision.status_code == 503
        assert decision.body["tenant_resolved"] is True
        assert decision.body["joined"] is False
        assert "Telnyx" in decision.body["reason"]
        assert opener.calls == []

    async def test_known_did_without_xai_refuses_and_does_not_join(
        self, monkeypatch, bind_db, two_tenants: tuple[UUID, UUID]
    ):
        del two_tenants
        monkeypatch.setenv("XAI_WEBHOOK_SECRET", XAI_WEBHOOK_SECRET)
        monkeypatch.setenv("TELNYX_API_KEY", "KEY_NOT_A_REAL_TELNYX_KEY")
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        opener = RecordingOpener()
        body = inbound_payload(call_id="call_no_xai")
        decision = await accept_inbound_call(
            body, sign_xai(body, webhook_id="msg_no_xai"), engine=bind_db, opener=opener
        )
        assert decision.status_code == 503
        assert decision.body["tenant_resolved"] is True
        assert "xAI" in decision.body["reason"]
        assert opener.calls == []


class TestWebhookRules:
    async def test_a_stale_signature_is_rejected(
        self, inbound_env, two_tenants: tuple[UUID, UUID]
    ):
        del two_tenants
        opener = RecordingOpener()
        body = inbound_payload(call_id="call_stale")
        decision = await accept_inbound_call(
            body,
            sign_xai(body, webhook_id="msg_stale", at=NOW - 301),
            engine=inbound_env,
            opener=opener,
            now=NOW,
        )
        assert decision.status_code == 401
        assert opener.calls == []

    async def test_the_same_webhook_id_is_idempotent(
        self, inbound_env, two_tenants: tuple[UUID, UUID]
    ):
        del two_tenants
        opener = RecordingOpener()
        body = inbound_payload(call_id="call_dup")
        headers = sign_xai(body, webhook_id="msg_dup")
        first = await accept_inbound_call(body, headers, engine=inbound_env, opener=opener)
        second = await accept_inbound_call(body, headers, engine=inbound_env, opener=opener)
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.body["status"] == "duplicate"
        assert len(opener.calls) == 1

    async def test_default_opener_fails_closed_without_crashing(
        self, inbound_env, two_tenants: tuple[UUID, UUID]
    ):
        """session.py is Sam's. The surrounding path must not crash."""
        del two_tenants
        bind_inbound_opener(None)
        body = inbound_payload(call_id="call_sams")
        decision = await accept_inbound_call(
            body, sign_xai(body, webhook_id="msg_sams"), engine=inbound_env
        )
        assert decision.status_code == 503
        assert decision.body["tenant_resolved"] is True
        assert decision.body["joined"] is False
        assert decision.handed_off is False


class TestHttpFrontDoor:
    async def test_http_unknown_did_is_404(self, inbound_env, two_tenants, monkeypatch):
        del two_tenants
        monkeypatch.setenv("XAI_WEBHOOK_SECRET", XAI_WEBHOOK_SECRET)
        client = TestClient(create_app())
        body = inbound_payload(to_did=UNKNOWN_DID, call_id="call_http_unknown")
        response = client.post(
            "/webhooks/xai/inbound",
            content=body,
            headers=sign_xai(body, webhook_id="msg_http_unknown"),
        )
        assert response.status_code == 404
        assert response.json()["joined"] is False
        # inbound_env used so DATABASE_URL/get_engine are bound
        assert inbound_env is not None
