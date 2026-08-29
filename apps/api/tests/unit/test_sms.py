from __future__ import annotations

import pytest

from mabel.sms import SmsError, notify_owner


def test_notify_owner_fails_closed_without_telnyx(monkeypatch) -> None:
    monkeypatch.delenv("TELNYX_API_KEY", raising=False)
    with pytest.raises(SmsError, match="Telnyx is not configured"):
        notify_owner(body="Burst pipe at the example house.", to="+12165550199")
