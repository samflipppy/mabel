"""Shop packet: structured facts for one tenant.

This is client onboarding. Shop name, vertical, timezone, owner SMS, after-hours
window, service-area zips, optional greeting notes, optional xAI Voice Agent id.
Greeting notes may not hold dollar-looking text. Money, if it ever lands on a shop,
stays NUMERIC(12,2) in SQL and Decimal in Python. An LLM does not write this packet.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import time
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mabel.platform.phones import E164_RE, normalize_e164

DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_AFTER_HOURS_START = time(17, 0)
DEFAULT_AFTER_HOURS_END = time(8, 0)

# $199, "dollars", 89.00 — greeting notes must not carry a figure Mabel could read aloud.
_DOLLARISH = re.compile(
    r"""
    \$
    | \b(?:usd|dollars?|cents?)\b
    | \b\d{1,3}(?:,\d{3})+\.\d{2}\b
    | \b\d+\.\d{2}\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


class PacketError(ValueError):
    """This shop packet is not safe to inject."""


def normalize_zip(raw: str) -> str:
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 5:
        raise PacketError("Mabel needs a five-digit zip.")
    return digits[:5]


def reject_dollar_text(text: str, *, field: str = "greeting notes") -> None:
    if _DOLLARISH.search(text):
        raise PacketError(f"Mabel will not store a dollar figure in {field}.")


def _database_url() -> str | None:
    value = os.environ.get("DATABASE_URL")
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@dataclass(frozen=True)
class ShopPacket:
    tenant_id: UUID
    name: str
    vertical: str
    owner_sms_e164: str
    timezone: str = DEFAULT_TIMEZONE
    after_hours_start: time = DEFAULT_AFTER_HOURS_START
    after_hours_end: time = DEFAULT_AFTER_HOURS_END
    service_area_zips: tuple[str, ...] = ()
    greeting_notes: str | None = None
    xai_voice_agent_id: str | None = None

    def __post_init__(self) -> None:
        name = self.name.strip()
        vertical = self.vertical.strip()
        if not name:
            raise PacketError("Mabel needs the shop name.")
        if not vertical:
            raise PacketError("Mabel needs the shop's trade.")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "vertical", vertical)

        tz = (self.timezone or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
        try:
            ZoneInfo(tz)
        except ZoneInfoNotFoundError as exc:
            raise PacketError("Mabel needs a real timezone.") from exc
        object.__setattr__(self, "timezone", tz)

        sms = normalize_e164(self.owner_sms_e164)
        if not E164_RE.fullmatch(sms):
            raise PacketError("Mabel needs the owner's mobile in E.164.")
        object.__setattr__(self, "owner_sms_e164", sms)

        notes = self.greeting_notes
        if notes is not None:
            notes = notes.strip()
            if not notes:
                notes = None
            else:
                reject_dollar_text(notes)
        object.__setattr__(self, "greeting_notes", notes)

        agent_id = self.xai_voice_agent_id
        if agent_id is not None:
            agent_id = agent_id.strip() or None
        object.__setattr__(self, "xai_voice_agent_id", agent_id)

        zips = tuple(normalize_zip(zip_code) for zip_code in self.service_area_zips)
        object.__setattr__(self, "service_area_zips", zips)


_packets: dict[UUID, ShopPacket] = {}


def register_packet(packet: ShopPacket) -> None:
    _packets[packet.tenant_id] = packet


def reset_packets() -> None:
    _packets.clear()


def memory_packet(tenant_id: UUID) -> ShopPacket | None:
    return _packets.get(tenant_id)


def packet_for(tenant_id: UUID) -> ShopPacket | None:
    """Load this tenant's packet. Database when DATABASE_URL is set, memory otherwise."""
    if _database_url():
        from mabel.platform.db import tenant_scope
        from mabel.shops.store import fetch_shop_packet

        try:
            with tenant_scope(tenant_id) as conn:
                return fetch_shop_packet(conn, tenant_id)
        except PacketError:
            return None
    return memory_packet(tenant_id)
