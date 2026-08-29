"""Shop packet SQL. Runs inside tenant_scope. RLS matches on app.tenant_id."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from uuid import UUID

from mabel.platform.tenancy import DuplicateDidError
from mabel.shops.packet import PacketError, ShopPacket, normalize_zip

SHOP_STATUS_DRAFT = "draft"


def persist_onboarded_shop(conn: Any, packet: ShopPacket, inbound_did: str) -> None:
    """INSERT tenant, inbound DID, and zips. Caller already SET LOCAL app.tenant_id.

    inbound_dids RLS WITH CHECK matches tenant_id to that setting, so one
    transaction is enough. The app role stays ordinary. No extra SQL function.
    """
    existing = conn.execute(
        "SELECT app.resolve_tenant_from_did(%s)",
        (inbound_did,),
    ).fetchone()
    if existing is not None and existing[0] is not None:
        raise DuplicateDidError("Mabel already answers this number.")

    conn.execute(
        """
        INSERT INTO tenants (
            id, name, vertical, status, timezone, owner_sms_e164,
            after_hours_start, after_hours_end, greeting_notes,
            xai_voice_agent_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            str(packet.tenant_id),
            packet.name,
            packet.vertical,
            SHOP_STATUS_DRAFT,
            packet.timezone,
            packet.owner_sms_e164,
            packet.after_hours_start,
            packet.after_hours_end,
            packet.greeting_notes,
            packet.xai_voice_agent_id,
        ),
    )
    try:
        conn.execute(
            "INSERT INTO inbound_dids (e164, tenant_id) VALUES (%s, %s)",
            (inbound_did, str(packet.tenant_id)),
        )
    except Exception as exc:
        if _is_unique_violation(exc):
            raise DuplicateDidError("Mabel already answers this number.") from exc
        raise

    for zip_code in dict.fromkeys(packet.service_area_zips):
        conn.execute(
            "INSERT INTO service_area_zips (tenant_id, zip) VALUES (%s, %s)",
            (str(packet.tenant_id), zip_code),
        )


def _is_unique_violation(exc: BaseException) -> bool:
    return getattr(exc, "sqlstate", None) == "23505"


def fetch_shop_packet(conn: Any, tenant_id: UUID) -> ShopPacket:
    row = conn.execute(
        """
        SELECT id, name, vertical, timezone, owner_sms_e164,
               after_hours_start, after_hours_end, greeting_notes,
               xai_voice_agent_id
        FROM tenants
        WHERE id = %s
        """,
        (str(tenant_id),),
    ).fetchone()
    if row is None:
        raise PacketError("Mabel has no shop packet for this tenant.")
    zip_rows = conn.execute(
        "SELECT zip FROM service_area_zips WHERE retired_at IS NULL ORDER BY zip"
    ).fetchall()
    zips = tuple(normalize_zip(item[0]) for item in zip_rows)
    owner_sms = row[4]
    if owner_sms is None or not str(owner_sms).strip():
        raise PacketError("Mabel has no shop packet for this tenant.")
    agent_raw = row[8] if len(row) > 8 else None
    agent_id = None if agent_raw is None else str(agent_raw).strip() or None
    return ShopPacket(
        tenant_id=UUID(str(row[0])),
        name=str(row[1]),
        vertical=str(row[2]),
        timezone=str(row[3] or "America/New_York"),
        owner_sms_e164=str(owner_sms),
        after_hours_start=_as_time(row[5], time(17, 0)),
        after_hours_end=_as_time(row[6], time(8, 0)),
        service_area_zips=zips,
        greeting_notes=None if row[7] is None else str(row[7]),
        xai_voice_agent_id=agent_id,
    )


def fetch_shop_extras(conn: Any, tenant_id: UUID) -> tuple[ShopPacket, str, str | None]:
    """Packet plus status and inbound DID. Caller already SET LOCAL app.tenant_id."""
    packet = fetch_shop_packet(conn, tenant_id)
    status_row = conn.execute(
        "SELECT status FROM tenants WHERE id = %s",
        (str(tenant_id),),
    ).fetchone()
    did_row = conn.execute("SELECT e164 FROM inbound_dids").fetchone()
    status = str(status_row[0]) if status_row and status_row[0] is not None else SHOP_STATUS_DRAFT
    inbound = None if did_row is None or did_row[0] is None else str(did_row[0])
    return packet, status, inbound


def update_shop_packet(conn: Any, packet: ShopPacket, *, replace_zips: bool) -> None:
    """UPDATE packet columns. Caller already SET LOCAL app.tenant_id. No live flip."""
    existing = conn.execute(
        "SELECT id FROM tenants WHERE id = %s",
        (str(packet.tenant_id),),
    ).fetchone()
    if existing is None:
        raise PacketError("Mabel has no shop packet for this tenant.")
    conn.execute(
        """
        UPDATE tenants SET
            name = %s,
            timezone = %s,
            owner_sms_e164 = %s,
            after_hours_start = %s,
            after_hours_end = %s,
            greeting_notes = %s
        WHERE id = %s
        """,
        (
            packet.name,
            packet.timezone,
            packet.owner_sms_e164,
            packet.after_hours_start,
            packet.after_hours_end,
            packet.greeting_notes,
            str(packet.tenant_id),
        ),
    )
    if replace_zips:
        replace_service_area_zips(conn, packet.tenant_id, packet.service_area_zips)


def replace_service_area_zips(conn: Any, tenant_id: UUID, zips: tuple[str, ...]) -> None:
    """Retire active zips, then insert or un-retire the new list. No DELETE."""
    conn.execute(
        "UPDATE service_area_zips SET retired_at = now() WHERE retired_at IS NULL"
    )
    for zip_code in dict.fromkeys(zips):
        found = conn.execute(
            "SELECT zip FROM service_area_zips WHERE zip = %s",
            (zip_code,),
        ).fetchone()
        if found is None:
            conn.execute(
                "INSERT INTO service_area_zips (tenant_id, zip) VALUES (%s, %s)",
                (str(tenant_id), zip_code),
            )
        else:
            conn.execute(
                "UPDATE service_area_zips SET retired_at = NULL WHERE zip = %s",
                (zip_code,),
            )


def _as_time(value: Any, default: time) -> time:
    if value is None:
        return default
    if isinstance(value, time):
        return value
    if isinstance(value, datetime):
        return value.time()
    text = str(value)
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise PacketError("Mabel could not read this shop's after-hours window.")
