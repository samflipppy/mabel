"""7am recap queue. Non-emergencies wait. This PR does not send the recap.

recap_at is the next 7am in the shop's timezone. Deterministic. No model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from mabel.shops.packet import DEFAULT_TIMEZONE, packet_for

_clock: datetime | None = None


@dataclass(frozen=True)
class RecapItem:
    tenant_id: UUID
    recap_at: datetime


_queue: list[RecapItem] = []


def set_clock(now: datetime | None) -> None:
    """Tests pin 'now' so next-7am is stable. Production leaves this unset."""
    global _clock
    _clock = now


def current_clock() -> datetime:
    if _clock is not None:
        return _clock
    return datetime.now(timezone.utc)


def recap_queue() -> list[RecapItem]:
    return list(_queue)


def reset_recap() -> None:
    _queue.clear()
    set_clock(None)


def next_7am_local(*, tz_name: str, now: datetime) -> datetime:
    zone = ZoneInfo(tz_name)
    local = now if now.tzinfo is not None else now.replace(tzinfo=zone)
    local = local.astimezone(zone)
    candidate = local.replace(hour=7, minute=0, second=0, microsecond=0)
    if local >= candidate:
        candidate = candidate + timedelta(days=1)
    return candidate


def queue_morning_recap(tenant_id: UUID) -> RecapItem:
    """Enqueue a 7am recap. Do not SMS. Do not call Telnyx."""
    packet = packet_for(tenant_id)
    tz_name = packet.timezone if packet is not None else DEFAULT_TIMEZONE
    item = RecapItem(
        tenant_id=tenant_id,
        recap_at=next_7am_local(tz_name=tz_name, now=current_clock()),
    )
    _queue.append(item)
    return item
