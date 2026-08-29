from __future__ import annotations

from datetime import time
from uuid import uuid4

import pytest

from mabel.mcp.tools import bind_tenant, call_tool, reset_store, reset_tenant
from mabel.shops.packet import (
    DEFAULT_TIMEZONE,
    PacketError,
    ShopPacket,
    register_packet,
    reset_packets,
)


def setup_function() -> None:
    reset_store()
    reset_packets()


def _packet(**overrides) -> ShopPacket:
    values = {
        "tenant_id": uuid4(),
        "name": "Example Plumbing",
        "vertical": "plumbing",
        "owner_sms_e164": "+12165550199",
        "service_area_zips": ("44107",),
    }
    values.update(overrides)
    return ShopPacket(**values)


def test_timezone_defaults_to_america_new_york() -> None:
    packet = _packet()
    assert packet.timezone == DEFAULT_TIMEZONE
    assert packet.timezone == "America/New_York"
    assert packet.after_hours_start == time(17, 0)
    assert packet.after_hours_end == time(8, 0)
    assert packet.greeting_notes is None
    assert packet.xai_voice_agent_id is None


def test_greeting_notes_without_money_are_kept() -> None:
    packet = _packet(greeting_notes="Ask how the dog is. Do not quote.")
    assert packet.greeting_notes == "Ask how the dog is. Do not quote."


@pytest.mark.parametrize(
    "notes",
    [
        "Tell them it's $99 after hours.",
        "After-hours rate is 89.00",
        "We charge ninety dollars",
        "Trip fee is 1,200.00",
        "Say USD 50 for the visit",
    ],
)
def test_dollar_looking_greeting_notes_are_rejected(notes: str) -> None:
    with pytest.raises(PacketError, match="dollar"):
        _packet(greeting_notes=notes)


def test_two_tenants_service_area_isolation() -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    register_packet(_packet(tenant_id=tenant_a, name="Shop A", service_area_zips=("44107",)))
    register_packet(_packet(tenant_id=tenant_b, name="Shop B", service_area_zips=("44102",)))

    bound_a = bind_tenant(tenant_a)
    try:
        in_a = call_tool("get_service_area", {"zip_code": "44107"})
        out_a = call_tool("get_service_area", {"zip_code": "44102"})
    finally:
        reset_tenant(bound_a)

    bound_b = bind_tenant(tenant_b)
    try:
        in_b = call_tool("get_service_area", {"zip_code": "44102"})
        out_b = call_tool("get_service_area", {"zip_code": "44107"})
    finally:
        reset_tenant(bound_b)

    assert in_a == {"zip": "44107", "in_area": True}
    assert out_a == {"zip": "44102", "in_area": False}
    assert in_b == {"zip": "44102", "in_area": True}
    assert out_b == {"zip": "44107", "in_area": False}


def test_get_service_area_ignores_tenant_id_argument() -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    register_packet(_packet(tenant_id=tenant_a, service_area_zips=("44107",)))
    register_packet(_packet(tenant_id=tenant_b, service_area_zips=("44102",)))

    bound = bind_tenant(tenant_a)
    try:
        result = call_tool(
            "get_service_area",
            {"zip_code": "44102", "tenant_id": str(tenant_b)},
        )
    finally:
        reset_tenant(bound)

    assert result == {"zip": "44102", "in_area": False}


def test_missing_packet_is_not_in_area() -> None:
    bound = bind_tenant(uuid4())
    try:
        result = call_tool("get_service_area", {"zip_code": "44107"})
    finally:
        reset_tenant(bound)
    assert result["in_area"] is False
