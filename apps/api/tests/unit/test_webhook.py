from __future__ import annotations

import json
import time
from uuid import uuid4

from fastapi.testclient import TestClient

from mabel.app import create_app
from mabel.platform.tenancy import Tenant, directory, reset_directory
from mabel.voice.model import VOICE_MODEL
from mabel.voice.signatures import sign_webhook

SECRET = "test-webhook-secret-not-a-real-key"
DID = "+12165550199"


def _client(
    monkeypatch,
    *,
    xai_key: str | None = None,
    telnyx_key: str | None = None,
) -> TestClient:
    reset_directory()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    directory().register(
        DID,
        Tenant(id=uuid4(), vertical="plumbing", name="Example Plumbing"),
    )
    monkeypatch.setenv("XAI_WEBHOOK_SECRET", SECRET)
    if xai_key:
        monkeypatch.setenv("XAI_API_KEY", xai_key)
    else:
        monkeypatch.delenv("XAI_API_KEY", raising=False)
    if telnyx_key:
        monkeypatch.setenv("TELNYX_API_KEY", telnyx_key)
    else:
        monkeypatch.delenv("TELNYX_API_KEY", raising=False)
    return TestClient(create_app())


def _payload() -> bytes:
    return json.dumps(
        {
            "object": "event",
            "id": "evt_test",
            "type": "realtime.call.incoming",
            "created_at": 1750000000,
            "data": {
                "call_id": "00000000-0000-0000-0000-000000000000",
                "sip_headers": [
                    {"name": "From", "value": "+12165550100"},
                    {"name": "To", "value": DID},
                ],
            },
        }
    ).encode("utf-8")


def _headers(body: bytes, secret: str = SECRET) -> dict[str, str]:
    webhook_id = "msg_test"
    timestamp = str(int(time.time()))
    return {
        "webhook-id": webhook_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": sign_webhook(
            webhook_id=webhook_id,
            webhook_timestamp=timestamp,
            body=body,
            secret=secret,
        ),
        "content-type": "application/json",
    }


def test_webhook_fails_closed_without_xai_key(monkeypatch) -> None:
    client = _client(monkeypatch, telnyx_key="present-but-not-logged")
    body = _payload()
    response = client.post("/voice/webhook", content=body, headers=_headers(body))
    assert response.status_code == 503
    assert "xAI is not configured" in response.json()["detail"]
    assert "sk-" not in response.text
    assert SECRET not in response.text
    assert "present-but-not-logged" not in response.text


def test_webhook_fails_closed_without_telnyx_key(monkeypatch) -> None:
    client = _client(monkeypatch, xai_key="present-but-not-logged")
    body = _payload()
    response = client.post("/voice/webhook", content=body, headers=_headers(body))
    assert response.status_code == 503
    assert "Telnyx is not configured" in response.json()["detail"]
    assert "present-but-not-logged" not in response.text


def test_webhook_rejects_bad_signature(monkeypatch) -> None:
    client = _client(monkeypatch)
    body = _payload()
    headers = _headers(body)
    headers["webhook-signature"] = "v1,AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    response = client.post("/voice/webhook", content=body, headers=headers)
    assert response.status_code == 401


def test_webhook_unknown_did(monkeypatch) -> None:
    client = _client(monkeypatch)
    body = json.dumps(
        {
            "data": {
                "call_id": "00000000-0000-0000-0000-000000000000",
                "sip_headers": [{"name": "To", "value": "+12165550999"}],
            }
        }
    ).encode()
    response = client.post("/voice/webhook", content=body, headers=_headers(body))
    assert response.status_code == 404


def test_webhook_does_not_join_even_when_key_present(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        xai_key="present-but-not-logged",
        telnyx_key="also-present-not-logged",
    )
    body = _payload()
    response = client.post("/voice/webhook", content=body, headers=_headers(body))
    assert response.status_code == 200
    data = response.json()
    assert data["joined"] is False
    assert data["live"] is False
    assert data["voice_model"] == VOICE_MODEL
    assert data["tenant_resolved"] is True
    assert "present-but-not-logged" not in response.text
    assert "also-present-not-logged" not in response.text


def test_webhook_without_signing_secret_fails_closed(monkeypatch) -> None:
    monkeypatch.delenv("XAI_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("TELNYX_API_KEY", raising=False)
    client = TestClient(create_app())
    body = _payload()
    response = client.post("/voice/webhook", content=body, headers=_headers(body))
    assert response.status_code == 503
    assert "Webhook signing is not configured" in response.json()["detail"]
