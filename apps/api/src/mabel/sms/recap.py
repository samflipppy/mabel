"""7am recap queue. Non-emergencies wait.

recap_at is the next 7am in the shop's timezone. Deterministic. No model.
Send due items with `python -m mabel.sms.recap_send`. Not a cron.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from mabel.shops.packet import DEFAULT_TIMEZONE, packet_for

_clock: datetime | None = None


@dataclass(frozen=True)
class RecapItem:
    tenant_id: UUID
    recap_at: datetime
    id: UUID = field(default_factory=uuid4)
    lead_id: UUID | None = None
    sent_at: datetime | None = None


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


def queue_morning_recap(tenant_id: UUID, *, lead_id: UUID | None = None) -> RecapItem:
    """Enqueue a 7am recap. Do not SMS. Do not call Telnyx."""
    packet = packet_for(tenant_id)
    tz_name = packet.timezone if packet is not None else DEFAULT_TIMEZONE
    item = RecapItem(
        tenant_id=tenant_id,
        recap_at=next_7am_local(tz_name=tz_name, now=current_clock()),
        lead_id=lead_id,
    )
    _queue.append(item)
    if _database_url():
        from mabel.sms.recap_store import persist_recap

        persist_recap(item)
    return item


def replace_recap(item: RecapItem) -> None:
    """Swap a queue item in place (sent_at). Does not delete."""
    for index, current in enumerate(_queue):
        if current.id == item.id:
            _queue[index] = item
            return
    _queue.append(item)


def _database_url() -> str | None:
    import os

    value = os.environ.get("DATABASE_URL")
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
