"""Onboard a shop. This is the write path.

Generate the tenant UUID first, then BEGIN + SET LOCAL app.tenant_id, then
INSERT tenant, inbound DID, and zips. Status starts as draft. Nothing here
takes a shop live. The model does not call this.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import time
from typing import Any
from uuid import UUID, uuid4

from mabel.platform.phones import E164_RE, normalize_e164
from mabel.platform.tenancy import (
    DuplicateDidError,
    MemoryDidDirectory,
    Tenant,
    UnknownDidError,
    directory,
)
from mabel.shops.packet import (
    DEFAULT_TIMEZONE,
    PacketError,
    ShopPacket,
    register_packet,
)
from mabel.shops.store import SHOP_STATUS_DRAFT, persist_onboarded_shop

__all__ = [
    "DuplicateDidError",
    "OnboardedShop",
    "onboard_shop",
]


@dataclass(frozen=True)
class OnboardedShop:
    tenant_id: UUID
    inbound_did: str
    status: str
    packet: ShopPacket


def onboard_shop(
    *,
    name: str,
    vertical: str,
    inbound_did: str,
    owner_sms_e164: str,
    service_area_zips: Sequence[str] = (),
    timezone: str = DEFAULT_TIMEZONE,
    after_hours_start: time | None = None,
    after_hours_end: time | None = None,
    greeting_notes: str | None = None,
    conn: Any | None = None,
) -> OnboardedShop:
    """Create a shop as draft. Tenant id is minted here, never taken from a caller."""
    tenant_id = uuid4()
    did = _inbound_did(inbound_did)
    packet_kwargs: dict[str, Any] = {}
    if after_hours_start is not None:
        packet_kwargs["after_hours_start"] = after_hours_start
    if after_hours_end is not None:
        packet_kwargs["after_hours_end"] = after_hours_end
    packet = ShopPacket(
        tenant_id=tenant_id,
        name=name,
        vertical=vertical,
        owner_sms_e164=owner_sms_e164,
        timezone=timezone or DEFAULT_TIMEZONE,
        service_area_zips=tuple(service_area_zips),
        greeting_notes=greeting_notes,
        **packet_kwargs,
    )

    if conn is not None or _database_url():
        _persist_postgres(packet, did, conn)
    else:
        _persist_memory(packet, did)

    return OnboardedShop(
        tenant_id=packet.tenant_id,
        inbound_did=did,
        status=SHOP_STATUS_DRAFT,
        packet=packet,
    )


def _inbound_did(raw: str) -> str:
    if raw is None or not str(raw).strip():
        raise PacketError("Mabel needs the inbound number in E.164.")
    did = normalize_e164(str(raw))
    if not E164_RE.fullmatch(did):
        raise PacketError("Mabel needs the inbound number in E.164.")
    return did


def _persist_memory(packet: ShopPacket, inbound_did: str) -> None:
    found = directory()
    try:
        found.resolve(inbound_did)
    except UnknownDidError:
        pass
    else:
        raise DuplicateDidError("Mabel already answers this number.")
    if not isinstance(found, MemoryDidDirectory):
        raise PacketError("Mabel cannot register a shop without a database.")
    register_packet(packet)
    found.register(
        inbound_did,
        Tenant(
            id=packet.tenant_id,
            vertical=packet.vertical,
            name=packet.name,
            packet=packet,
            status=SHOP_STATUS_DRAFT,
        ),
    )


def _persist_postgres(packet: ShopPacket, inbound_did: str, conn: Any | None) -> None:
    from mabel.platform.db import tenant_scope

    with tenant_scope(packet.tenant_id, conn) as scoped:
        persist_onboarded_shop(scoped, packet, inbound_did)


def _database_url() -> str | None:
    value = os.environ.get("DATABASE_URL")
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
