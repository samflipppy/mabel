from __future__ import annotations

import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(API_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "packages" / "verticals"))

import pytest  # noqa: E402

from mabel.sms import FakeTelnyxSmsClient, bind_sms_client  # noqa: E402
from mabel.voice.session import FakeSessionTransport, bind_session_transport  # noqa: E402


@pytest.fixture(autouse=True)
def fake_telnyx_client():
    """Tests never POST to Telnyx. 10DLC is not cleared; no live send."""
    fake = FakeTelnyxSmsClient()
    bind_sms_client(fake)
    try:
        yield fake
    finally:
        bind_sms_client(None)


@pytest.fixture(autouse=True)
def fake_session_transport():
    """Tests never open a WebSocket to xAI."""
    fake = FakeSessionTransport()
    bind_session_transport(fake)
    try:
        yield fake
    finally:
        bind_session_transport(None)
