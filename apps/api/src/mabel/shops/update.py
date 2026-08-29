"""Patch shop packet fields. Owner settings. Not emergency rules. Not live."""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import time
from typing import Any
from uuid import UUID

from mabel.platform.tenancy import MemoryDidDirectory, Tenant, directory
from mabel.shops.packet import PacketError, ShopPacket, packet_for, register_packet
from mabel.shops.store import SHOP_STATUS_DRAFT, fetch_shop_extras, update_shop_packet


class UnknownShopError(LookupError):
    """This tenant is not one of ours."""


def update_shop(
    tenant_id: UUID,
    *,
    name: str | None = None,
    timezone: str | None = None,
    owner_sms_e164: str | None = None,
    after_hours_start: time | None = None,
    after_hours_end: time | None = None,
    service_area_zips: Sequence[str] | None = None,
    greeting_notes: str | None = None,
    greeting_notes_set: bool = False,
    conn: Any | None = None,
) -> ShopPacket:
    """Update packet fields. Does not change vertical rules or take a shop live."""
    current = _current_packet(tenant_id, conn)
    notes = greeting_notes if greeting_notes_set else current.greeting_notes
    zips = (
        tuple(service_area_zips)
        if service_area_zips is not None
        else current.service_area_zips
    )
    packet = ShopPacket(
        tenant_id=current.tenant_id,
        name=name if name is not None else current.name,
        vertical=current.vertical,
        owner_sms_e164=owner_sms_e164 if owner_sms_e164 is not None else current.owner_sms_e164,
        timezone=timezone if timezone is not None else current.timezone,
        after_hours_start=(
            after_hours_start if after_hours_start is not None else current.after_hours_start
        ),
        after_hours_end=(
            after_hours_end if after_hours_end is not None else current.after_hours_end
        ),
        service_area_zips=zips,
        greeting_notes=notes,
        xai_voice_agent_id=current.xai_voice_agent_id,
    )
    if conn is not None or _database_url():
        _persist_postgres(packet, conn, zips_replaced=service_area_zips is not None)
    else:
        _persist_memory(packet)
    return packet


def load_shop(tenant_id: UUID, conn: Any | None = None) -> tuple[ShopPacket, str, str | None]:
    """Packet, status, inbound DID. Other tenants are not visible."""
    if conn is not None or _database_url():
        from mabel.platform.db import tenant_scope

        try:
            with tenant_scope(tenant_id, conn) as scoped:
                extras = fetch_shop_extras(scoped, tenant_id)
        except PacketError as exc:
            raise UnknownShopError(str(exc)) from exc
        return extras
    packet = packet_for(tenant_id)
    if packet is None:
        raise UnknownShopError("Mabel has no shop packet for this tenant.")
    inbound = _memory_did(tenant_id)
    found = directory()
    status = SHOP_STATUS_DRAFT
    if inbound and isinstance(found, MemoryDidDirectory):
        try:
            status = found.resolve(inbound).status
        except Exception:
            status = SHOP_STATUS_DRAFT
    return packet, status, inbound


def _current_packet(tenant_id: UUID, conn: Any | None) -> ShopPacket:
    if conn is not None or _database_url():
        packet, _status, _did = load_shop(tenant_id, conn)
        return packet
    packet = packet_for(tenant_id)
    if packet is None:
        raise UnknownShopError("Mabel has no shop packet for this tenant.")
    return packet


def _persist_memory(packet: ShopPacket) -> None:
    register_packet(packet)
    found = directory()
    if not isinstance(found, MemoryDidDirectory):
        raise PacketError("Mabel cannot update a shop without a database.")
    did = _memory_did(packet.tenant_id)
    if did is None:
        raise UnknownShopError("Mabel has no shop packet for this tenant.")
    existing = found.resolve(did)
    found.register(
        did,
        Tenant(
            id=packet.tenant_id,
            vertical=packet.vertical,
            name=packet.name,
            packet=packet,
            status=existing.status,
        ),
    )


def _persist_postgres(packet: ShopPacket, conn: Any | None, *, zips_replaced: bool) -> None:
    from mabel.platform.db import tenant_scope

    with tenant_scope(packet.tenant_id, conn) as scoped:
        update_shop_packet(scoped, packet, replace_zips=zips_replaced)


def _memory_did(tenant_id: UUID) -> str | None:
    found = directory()
    if not isinstance(found, MemoryDidDirectory):
        return None
    return found.did_for(tenant_id)


def _database_url() -> str | None:
    value = os.environ.get("DATABASE_URL")
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
