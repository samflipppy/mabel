from __future__ import annotations

import json
import time
from uuid import uuid4

from fastapi.testclient import TestClient

from mabel.app import create_app
from mabel.mcp.tokens import parse_tenant_token
from mabel.platform.tenancy import Tenant, directory, reset_directory
from mabel.shops.packet import ShopPacket, register_packet, reset_packets
from mabel.voice.archive import fetch_archives, reset_archives
from mabel.voice.model import OPENING_DISCLOSURE, VOICE_MODEL
from mabel.voice.session import (
    WebsocketSessionTransport,
    finish_session,
    reset_sessions,
)
from mabel.voice.webhook import AGENT_LIVE

SECRET = "test-webhook-secret-not-a-real-key"
MCP_SECRET = "unit-test-mcp-token-secret"
DID = "+12165550199"


def _client(
    monkeypatch,
    *,
    xai_key: str | None = None,
    telnyx_key: str | None = None,
):
    reset_directory()
    reset_packets()
    reset_archives()
    reset_sessions()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    tenant_id = uuid4()
    packet = ShopPacket(
        tenant_id=tenant_id,
        name="Example Plumbing",
        vertical="plumbing",
        owner_sms_e164="+12165550111",
        service_area_zips=("44107",),
        greeting_notes="Ask how the dog is.",
    )
    register_packet(packet)
    directory().register(
        DID,
        Tenant(
            id=tenant_id,
            vertical="plumbing",
            name="Example Plumbing",
            packet=packet,
        ),
    )
    monkeypatch.setenv("XAI_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("MABEL_MCP_TOKEN_SECRET", MCP_SECRET)
    if xai_key:
        monkeypatch.setenv("XAI_API_KEY", xai_key)
    else:
        monkeypatch.delenv("XAI_API_KEY", raising=False)
    if telnyx_key:
        monkeypatch.setenv("TELNYX_API_KEY", telnyx_key)
    else:
        monkeypatch.delenv("TELNYX_API_KEY", raising=False)
    return TestClient(create_app()), tenant_id


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
    from mabel.voice.signatures import sign_webhook

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


def test_webhook_fails_closed_without_xai_key(monkeypatch, fake_session_transport) -> None:
    client, _tenant = _client(monkeypatch, telnyx_key="present-but-not-logged")
    body = _payload()
    response = client.post("/voice/webhook", content=body, headers=_headers(body))
    assert response.status_code == 503
    assert "xAI is not configured" in response.json()["detail"]
    assert "sk-" not in response.text
    assert SECRET not in response.text
    assert "present-but-not-logged" not in response.text
    assert fake_session_transport.joined is False
    assert fake_session_transport.sent == []


def test_webhook_fails_closed_without_telnyx_key(monkeypatch, fake_session_transport) -> None:
    client, _tenant = _client(monkeypatch, xai_key="present-but-not-logged")
    body = _payload()
    response = client.post("/voice/webhook", content=body, headers=_headers(body))
    assert response.status_code == 503
    assert "Telnyx is not configured" in response.json()["detail"]
    assert "present-but-not-logged" not in response.text
    assert fake_session_transport.joined is False


def test_webhook_rejects_bad_signature(monkeypatch) -> None:
    client, _tenant = _client(monkeypatch)
    body = _payload()
    headers = _headers(body)
    headers["webhook-signature"] = "v1,AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    response = client.post("/voice/webhook", content=body, headers=headers)
    assert response.status_code == 401


def test_webhook_unknown_did(monkeypatch) -> None:
    client, _tenant = _client(monkeypatch)
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


def test_webhook_joins_fake_session_when_keys_present(
    monkeypatch, fake_session_transport
) -> None:
    client, tenant_id = _client(
        monkeypatch,
        xai_key="present-but-not-logged",
        telnyx_key="also-present-not-logged",
    )
    body = _payload()
    response = client.post("/voice/webhook", content=body, headers=_headers(body))
    assert response.status_code == 200
    data = response.json()
    assert data["joined"] is True
    assert data["live"] is False
    assert AGENT_LIVE is False
    assert data["voice_model"] == VOICE_MODEL
    assert data["tenant_resolved"] is True
    assert "present-but-not-logged" not in response.text
    assert "also-present-not-logged" not in response.text
    assert MCP_SECRET not in response.text
    assert fake_session_transport.joined is True
    assert len(fake_session_transport.session_updates) == 1
    assert len(fake_session_transport.force_messages) == 1
    assert fake_session_transport.response_creates == []
    update = fake_session_transport.session_updates[0]
    session = update["session"]
    assert session["voice"] == "eve"
    assert session["turn_detection"] == {"type": "server_vad"}
    assert session["audio"]["input"]["format"]["type"] == "audio/pcmu"
    assert session["audio"]["output"]["format"]["type"] == "audio/pcmu"
    assert "Example Plumbing" in session["instructions"]
    assert "$" not in session["instructions"]
    assert "89.00" not in session["instructions"]
    tools = session["tools"]
    assert len(tools) == 1
    assert tools[0]["type"] == "mcp"
    assert tools[0]["allowed_tools"] == [
        "lookup_customer",
        "get_service_area",
        "check_availability",
        "get_job_history",
        "create_lead",
        "escalate_emergency",
        "book_estimate",
        "log_note",
    ]
    token = tools[0]["authorization"].removeprefix("Bearer ")
    parsed = parse_tenant_token(token)
    assert parsed.tenant_id == tenant_id
    force = fake_session_transport.force_messages[0]
    assert force["item"]["type"] == "force_message"
    assert force["item"]["interruptible"] is False
    assert force["item"]["content"][0]["text"] == OPENING_DISCLOSURE


def test_fake_session_end_archives_only_that_tenant(
    monkeypatch, fake_session_transport
) -> None:
    client, tenant_id = _client(
        monkeypatch,
        xai_key="present-but-not-logged",
        telnyx_key="also-present-not-logged",
    )
    other = uuid4()
    body = _payload()
    response = client.post("/voice/webhook", content=body, headers=_headers(body))
    assert response.status_code == 200
    call_id = response.json()["call_id"]
    row = finish_session(call_id, transcript="Pat Example called about a slow drain.")
    assert row.tenant_id == tenant_id
    assert row.call_id == call_id
    assert "slow drain" in row.transcript
    assert row.recording_uri.startswith("placeholder:")
    assert "x.ai" not in row.recording_uri
    mine = fetch_archives(tenant_id)
    assert [item.id for item in mine] == [row.id]
    assert fetch_archives(other) == []
    assert AGENT_LIVE is False


def test_production_websocket_refuses_under_pytest(monkeypatch) -> None:
    import pytest

    from mabel.voice.session import SessionError

    monkeypatch.setenv("XAI_API_KEY", "present-but-not-logged")
    transport = WebsocketSessionTransport()

    async def _join() -> None:
        await transport.join(call_id="call-1", api_key="present-but-not-logged")

    with pytest.raises(SessionError, match="will not open a WebSocket from tests"):
        import asyncio

        asyncio.run(_join())


def test_webhook_without_signing_secret_fails_closed(monkeypatch) -> None:
    monkeypatch.delenv("XAI_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("TELNYX_API_KEY", raising=False)
    client = TestClient(create_app())
    body = _payload()
    response = client.post("/voice/webhook", content=body, headers=_headers(body))
    assert response.status_code == 503
    assert "Webhook signing is not configured" in response.json()["detail"]
