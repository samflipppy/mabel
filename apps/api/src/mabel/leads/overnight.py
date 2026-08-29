"""Overnight recap rows. Shop-safe fields only. No made-up leads."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from mabel.leads.memory import store
from mabel.leads.models import Lead
from mabel.leads.persist import fetch_leads, using_database
from mabel.shops.packet import DEFAULT_AFTER_HOURS_START, DEFAULT_TIMEZONE, packet_for
from mabel.sms.recap import current_clock


def overnight_start(
    *,
    tz_name: str,
    after_hours_start,
    now: datetime,
) -> datetime:
    """Most recent after-hours start at or before now, in the shop timezone."""
    zone = ZoneInfo(tz_name)
    local = now if now.tzinfo is not None else now.replace(tzinfo=zone)
    local = local.astimezone(zone)
    start = after_hours_start
    candidate = local.replace(
        hour=start.hour,
        minute=start.minute,
        second=0,
        microsecond=0,
    )
    if local < candidate:
        candidate = candidate - timedelta(days=1)
    return candidate


def leads_for_tenant(tenant_id: UUID) -> list[Lead]:
    if using_database():
        return fetch_leads(tenant_id)
    return store().for_tenant(tenant_id)


def overnight_leads(tenant_id: UUID, *, now: datetime | None = None) -> list[Lead]:
    packet = packet_for(tenant_id)
    tz_name = packet.timezone if packet is not None else DEFAULT_TIMEZONE
    hours = packet.after_hours_start if packet is not None else DEFAULT_AFTER_HOURS_START
    clock = now if now is not None else current_clock()
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    start = overnight_start(tz_name=tz_name, after_hours_start=hours, now=clock)
    found: list[Lead] = []
    for lead in leads_for_tenant(tenant_id):
        created = lead.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created >= start:
            found.append(lead)
    found.sort(key=lambda item: item.created_at)
    return found


def office_lead_view(lead: Lead) -> dict[str, object]:
    created = lead.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return {
        "time": created.isoformat(),
        "name": lead.name,
        "problem": lead.problem,
        "emergency": bool(lead.emergency_code),
        "sms_sent": bool(lead.sms_sent),
        "sms_reason": lead.sms_reason,
    }
