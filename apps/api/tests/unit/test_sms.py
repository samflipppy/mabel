from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from mabel_verticals.load import load_fixture, load_latest_rules

from mabel.mcp.tools import bind_tenant, call_tool, reset_store, reset_tenant, store
from mabel.shops.packet import ShopPacket, register_packet, reset_packets
from mabel.sms import (
    REASON_DOLLAR,
    REASON_TELNYX,
    SmsError,
    TelnyxHttpSmsClient,
    queue_morning_recap,
    recap_queue,
    reset_recap,
    reset_sms,
    send_due_recaps,
    sms_attempts,
)
from mabel.sms.recap import next_7am_local, set_clock
from mabel.voice.webhook import AGENT_LIVE

OWNER = "+12165550199"
CALLER = "+12165550100"


def setup_function() -> None:
    reset_store()
    reset_packets()
    reset_recap()
    reset_sms()


def _packet(tenant_id, **overrides) -> ShopPacket:
    values = {
        "tenant_id": tenant_id,
        "name": "Example Plumbing",
        "vertical": "plumbing",
        "owner_sms_e164": OWNER,
        "timezone": "America/New_York",
        "service_area_zips": ("44107",),
    }
    values.update(overrides)
    return ShopPacket(**values)


def _escalate(tenant_id, arguments: dict):
    bound = bind_tenant(tenant_id)
    try:
        return call_tool("escalate_emergency", arguments)
    finally:
        reset_tenant(bound)


def test_burst_pipe_queues_owner_sms_not_caller(monkeypatch, fake_telnyx_client) -> None:
    monkeypatch.setenv("TELNYX_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("TELNYX_FROM_E164", "+18005550199")
    fixture = load_fixture("plumbing_burst_pipe")
    tenant_id = uuid4()
    register_packet(_packet(tenant_id))
    captured = dict(fixture["input"]["captured"])
    assert captured["callback"] == CALLER
    assert OWNER != CALLER

    result = _escalate(
        tenant_id,
        {
            "vertical": fixture["vertical"],
            "utterances": fixture["input"]["utterances"],
            "captured": captured,
            "context": fixture["input"]["context"],
        },
    )

    assert result["escalated"] is True
    assert result["notify"] == "now"
    assert result["trigger"] == "BURST_PIPE"
    assert result["sms"]["sent"] is True
    assert result["sms"]["to"] == OWNER
    assert result["sms"]["to"] != CALLER
    leads = store().for_tenant(tenant_id)
    assert len(leads) == 1
    assert leads[0].callback == CALLER
    assert leads[0].emergency_code == "BURST_PIPE"
    assert len(fake_telnyx_client.sent) == 1
    assert fake_telnyx_client.sent[0]["to"] == OWNER
    assert fake_telnyx_client.sent[0]["to"] != CALLER
    assert fake_telnyx_client.sent[0]["from_e164"] != CALLER
    body = fake_telnyx_client.sent[0]["body"]
    assert "Example Plumbing" in body
    assert "BURST_PIPE" in body
    assert captured["address"] in body
    assert captured["problem"] in body
    assert CALLER in body


def test_missing_telnyx_key_keeps_lead_and_does_not_send(monkeypatch, fake_telnyx_client) -> None:
    monkeypatch.delenv("TELNYX_API_KEY", raising=False)
    fixture = load_fixture("plumbing_burst_pipe")
    tenant_id = uuid4()
    register_packet(_packet(tenant_id))

    result = _escalate(
        tenant_id,
        {
            "vertical": fixture["vertical"],
            "utterances": fixture["input"]["utterances"],
            "captured": fixture["input"]["captured"],
            "context": fixture["input"]["context"],
        },
    )

    assert result["escalated"] is True
    assert result["sms"]["sent"] is False
    assert result["sms"]["reason"] == REASON_TELNYX
    assert result["sms"]["reason"] == "telnyx not configured"
    assert result["sms"]["to"] == OWNER
    assert result["sms"]["to"] != CALLER
    assert len(store().for_tenant(tenant_id)) == 1
    assert fake_telnyx_client.sent == []
    attempts = sms_attempts()
    assert len(attempts) == 1
    assert attempts[0].sent is False
    assert attempts[0].to == OWNER
    assert attempts[0].to != CALLER


@pytest.mark.parametrize("field", ["problem", "address"])
def test_dollar_in_captured_field_aborts_sms_keeps_lead(
    monkeypatch, fake_telnyx_client, field: str
) -> None:
    monkeypatch.setenv("TELNYX_API_KEY", "test-not-a-real-key")
    fixture = load_fixture("plumbing_burst_pipe")
    tenant_id = uuid4()
    register_packet(_packet(tenant_id))
    captured = dict(fixture["input"]["captured"])
    captured[field] = f"{captured[field]} for $199"

    result = _escalate(
        tenant_id,
        {
            "vertical": fixture["vertical"],
            "utterances": fixture["input"]["utterances"],
            "captured": captured,
            "context": fixture["input"]["context"],
        },
    )

    assert result["escalated"] is True
    assert result["sms"]["sent"] is False
    assert result["sms"]["reason"] == REASON_DOLLAR
    assert result["sms"]["to"] == OWNER
    leads = store().for_tenant(tenant_id)
    assert len(leads) == 1
    assert getattr(leads[0], field).endswith("$199")
    assert fake_telnyx_client.sent == []


def test_recap_path_does_not_sms(monkeypatch, fake_telnyx_client) -> None:
    monkeypatch.setenv("TELNYX_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("TELNYX_FROM_E164", "+18005550199")
    fixture = load_fixture("plumbing_slow_drain_2am")
    tenant_id = uuid4()
    register_packet(_packet(tenant_id))
    called_at = datetime.fromisoformat(fixture["input"]["called_at"])
    set_clock(called_at)

    result = _escalate(
        tenant_id,
        {
            "vertical": fixture["vertical"],
            "utterances": fixture["input"]["utterances"],
            "captured": fixture["input"]["captured"],
            "context": fixture["input"]["context"],
        },
    )

    assert result["escalated"] is False
    assert result["notify"] == "recap_7am"
    assert result["sms"]["sent"] is False
    assert result["sms"]["reason"] == "recap_7am"
    assert store().leads == []
    assert fake_telnyx_client.sent == []
    assert sms_attempts() == []
    queued = recap_queue()
    assert len(queued) == 1
    assert queued[0].tenant_id == tenant_id
    expected = next_7am_local(tz_name="America/New_York", now=called_at)
    assert queued[0].recap_at == expected
    assert expected.hour == 7
    assert expected.date().isoformat() == "2026-01-16"
    assert result["recap"]["queued"] is True
    assert result["recap"]["recap_at"] == expected.isoformat()


def test_from_cannot_be_the_caller(monkeypatch, fake_telnyx_client) -> None:
    monkeypatch.setenv("TELNYX_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("TELNYX_FROM_E164", CALLER)
    fixture = load_fixture("plumbing_burst_pipe")
    tenant_id = uuid4()
    register_packet(_packet(tenant_id))

    result = _escalate(
        tenant_id,
        {
            "vertical": fixture["vertical"],
            "utterances": fixture["input"]["utterances"],
            "captured": fixture["input"]["captured"],
        },
    )

    assert result["escalated"] is True
    assert result["sms"]["sent"] is False
    assert result["sms"]["reason"] == "from cannot be the caller"
    assert len(store().for_tenant(tenant_id)) == 1
    assert fake_telnyx_client.sent == []


def test_production_client_refuses_under_pytest(monkeypatch) -> None:
    monkeypatch.setenv("TELNYX_API_KEY", "test-not-a-real-key")
    client = TelnyxHttpSmsClient()
    with pytest.raises(SmsError, match="will not call Telnyx from tests"):
        client.send_sms(to=OWNER, body="Burst pipe at the example house.", from_e164="+18005550199")


def test_agent_live_stays_false() -> None:
    assert AGENT_LIVE is False


def test_draft_verticals_stay_unverified() -> None:
    for vertical in ("hvac", "electrical", "restoration"):
        rules = load_latest_rules(vertical)
        assert rules["verified"] is False


def test_next_7am_after_seven_is_tomorrow() -> None:
    now = datetime.fromisoformat("2026-01-16T07:00:00-05:00")
    recap_at = next_7am_local(tz_name="America/New_York", now=now)
    assert recap_at.date().isoformat() == "2026-01-17"
    assert recap_at.hour == 7


def _overnight_leads(tenant_id) -> None:
    from mabel.leads.models import Lead
    from mabel.mcp.tools import store

    night = datetime.fromisoformat("2026-01-16T02:10:00-05:00")
    store().leads.append(
        Lead(
            id=uuid4(),
            tenant_id=tenant_id,
            name="Pat Example",
            address="100 Example Ave, Lakewood OH 44107",
            callback=CALLER,
            problem="slow drain",
            urgency="morning",
            source="google",
            created_at=night,
        )
    )
    store().leads.append(
        Lead(
            id=uuid4(),
            tenant_id=tenant_id,
            name="Other Example",
            address="200 Example Ave, Lakewood OH 44107",
            callback=CALLER,
            problem="burst pipe",
            urgency="now",
            source="google",
            emergency_code="BURST_PIPE",
            created_at=night,
        )
    )


def test_send_due_recaps_texts_owner_not_customer(monkeypatch, fake_telnyx_client) -> None:
    monkeypatch.setenv("TELNYX_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("TELNYX_FROM_E164", "+18005550199")
    tenant_id = uuid4()
    register_packet(_packet(tenant_id))
    set_clock(datetime.fromisoformat("2026-01-16T02:00:00-05:00"))
    queue_morning_recap(tenant_id)
    _overnight_leads(tenant_id)

    results = send_due_recaps(datetime.fromisoformat("2026-01-16T07:00:00-05:00"))
    assert len(results) == 1
    assert results[0].sent is True
    assert results[0].to == OWNER
    assert results[0].to != CALLER
    assert results[0].lead_count == 2
    assert results[0].emergency_count == 1
    assert "2 leads" in results[0].body
    assert "1 emergency" in results[0].body
    assert "$" not in results[0].body
    assert CALLER not in results[0].body
    assert len(fake_telnyx_client.sent) == 1
    assert fake_telnyx_client.sent[0]["to"] == OWNER
    assert fake_telnyx_client.sent[0]["to"] != CALLER
    assert recap_queue()[0].sent_at is not None
    attempts = sms_attempts()
    assert attempts[-1].sent is True
    assert attempts[-1].purpose == "recap_7am"
    assert attempts[-1].to == OWNER


def test_send_due_recaps_without_telnyx_keeps_queue_item(monkeypatch, fake_telnyx_client) -> None:
    monkeypatch.delenv("TELNYX_API_KEY", raising=False)
    tenant_id = uuid4()
    register_packet(_packet(tenant_id))
    set_clock(datetime.fromisoformat("2026-01-16T02:00:00-05:00"))
    item = queue_morning_recap(tenant_id)
    results = send_due_recaps(datetime.fromisoformat("2026-01-16T07:00:00-05:00"))
    assert results[0].sent is False
    assert results[0].reason == REASON_TELNYX
    assert results[0].to == OWNER
    assert fake_telnyx_client.sent == []
    queued = recap_queue()
    assert len(queued) == 1
    assert queued[0].id == item.id
    assert queued[0].sent_at is None


def test_send_due_recaps_skips_future_items(monkeypatch, fake_telnyx_client) -> None:
    monkeypatch.setenv("TELNYX_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("TELNYX_FROM_E164", "+18005550199")
    tenant_id = uuid4()
    register_packet(_packet(tenant_id))
    set_clock(datetime.fromisoformat("2026-01-16T02:00:00-05:00"))
    queue_morning_recap(tenant_id)
    results = send_due_recaps(datetime.fromisoformat("2026-01-16T06:00:00-05:00"))
    assert results == []
    assert fake_telnyx_client.sent == []
    assert recap_queue()[0].sent_at is None


def test_recap_send_entrypoint_exists() -> None:
    from mabel.sms import recap_send

    source = Path(recap_send.__file__).read_text(encoding="utf-8")
    assert "python -m mabel.sms.recap_send" in source
    assert "send_due_recaps" in source
    assert "cron" in source.lower()
