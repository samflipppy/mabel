"""Fakes used by the mocked E2E suite. Never a stand-in for a credential."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from uuid import UUID

from mabel_media.session import IncomingCall
from mabel_xai.webhooks import signed_payload

XAI_WEBHOOK_SECRET = "whsec_dGVzdC1zaWduaW5nLXNlY3JldC1ub3QtcmVhbA"
NOW = 1_800_000_000.0


class FakeObjectStore:
    """What post-call archival writes to when Supabase Storage is not there."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, path: str, data: bytes) -> None:
        self.objects[path] = data

    def get(self, path: str) -> bytes | None:
        return self.objects.get(path)


class RecordingOpener:
    """A session opener that records the IncomingCall and never opens a socket."""

    def __init__(self) -> None:
        self.calls: list[IncomingCall] = []

    async def __call__(self, call: IncomingCall) -> None:
        self.calls.append(call)


def sign_xai(
    body: bytes,
    *,
    webhook_id: str = "msg_e2e_1",
    at: float = NOW,
    secret: str = XAI_WEBHOOK_SECRET,
) -> dict[str, str]:
    timestamp = str(int(at))
    key = base64.b64decode(secret[len("whsec_") :])
    digest = hmac.new(key, signed_payload(webhook_id, timestamp, body), hashlib.sha256).digest()
    return {
        "webhook-id": webhook_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": "v1," + base64.b64encode(digest).decode(),
    }


def inbound_payload(
    *,
    to_did: str = "+12165550148",
    call_id: str = "call_e2e_1",
    from_did: str = "+12165550100",
    sip: bool = False,
) -> bytes:
    if sip:
        data = {
            "call_id": call_id,
            "sip_headers": [
                {"name": "From", "value": f"<sip:{from_did}@example.com>"},
                {
                    "name": "To",
                    "value": f'"Shop" <sip:{to_did}@sip.voice.x.ai;transport=tls>',
                },
            ],
        }
        payload: dict[str, Any] = {"type": "realtime.call.incoming", "data": data}
    else:
        payload = {
            "type": "realtime.call.incoming",
            "call_id": call_id,
            "to": to_did,
            "from": from_did,
        }
    return json.dumps(payload, separators=(",", ":")).encode()


def telnyx_keypair():
    from nacl.signing import SigningKey

    key = SigningKey.generate()
    public = base64.b64encode(bytes(key.verify_key)).decode()
    return key, public


def sign_telnyx(raw_body: bytes, signing_key, *, at: float | None = None) -> dict[str, str]:
    timestamp = str(int(time.time() if at is None else at))
    signed = signing_key.sign(timestamp.encode() + b"|" + raw_body)
    return {
        "telnyx-timestamp": timestamp,
        "telnyx-signature-ed25519": base64.b64encode(signed.signature).decode(),
    }


def telnyx_sms_body(*, event_id: str, from_number: str, text: str) -> bytes:
    return json.dumps(
        {
            "data": {
                "id": event_id,
                "event_type": "message.received",
                "payload": {
                    "text": text,
                    "from": {"phone_number": from_number},
                },
            }
        },
        separators=(",", ":"),
    ).encode()


def token_for(tenant_id: UUID, call_id: str = "call_e2e"):
    from mabel_mcp.tokens import mint_call_token, verify_call_token

    key = "a-test-signing-key-long-enough-to-be-accepted"
    return verify_call_token(mint_call_token(tenant_id, call_id, key=key), key=key)
