"""Load a shop packet inside tenant_scope. RLS matches on app.tenant_id."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from uuid import UUID

from mabel.shops.packet import PacketError, ShopPacket, normalize_zip


def fetch_shop_packet(conn: Any, tenant_id: UUID) -> ShopPacket:
    row = conn.execute(
        """
        SELECT id, name, vertical, timezone, owner_sms_e164,
               after_hours_start, after_hours_end, greeting_notes
        FROM tenants
        WHERE id = %s
        """,
        (str(tenant_id),),
    ).fetchone()
    if row is None:
        raise PacketError("Mabel has no shop packet for this tenant.")
    zip_rows = conn.execute("SELECT zip FROM service_area_zips ORDER BY zip").fetchall()
    zips = tuple(normalize_zip(item[0]) for item in zip_rows)
    owner_sms = row[4]
    if owner_sms is None or not str(owner_sms).strip():
        raise PacketError("Mabel has no shop packet for this tenant.")
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
