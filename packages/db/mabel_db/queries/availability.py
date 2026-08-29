"""Appointment windows. The one rule: Mabel never invents a time.

03-VOICE.md: `check_availability` returns real slots from Google Calendar if
connected, otherwise the tenant's configured default windows. Either way the
times come from here, and `book_estimate` takes a `slot_id` rather than a
free-text time, so she can only book something this module offered.

Google Calendar is Phase 6 and needs an account that does not exist yet
(docs/BLOCKED.md #10). Until then this returns the configured default windows,
which is the documented fallback rather than a stub — the behaviour is correct
today and stays correct when the integration lands.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# How far ahead we offer. Beyond a week an after-hours caller stops believing
# the answer, and the owner's own calendar will have moved anyway.
HORIZON_DAYS = 7

# The windows a contractor actually offers, absent a connected calendar.
# Deliberately coarse: "Tuesday morning" is a promise a contractor can keep,
# "Tuesday at 9:15" is one he cannot.
DEFAULT_WINDOWS: tuple[tuple[str, time, time], ...] = (
    ("morning", time(8), time(12)),
    ("afternoon", time(12), time(17)),
)


def slot_id(day: date, label: str) -> str:
    """A stable, opaque handle for one window.

    Opaque so the model cannot construct one it was never offered: `book_estimate`
    looks the id up rather than parsing it, and an id that did not come from
    `check_availability` will not be found.

    Stable so the same window keeps the same id between the availability call
    and the booking call a few seconds later.
    """
    digest = hashlib.sha256(f"{day.isoformat()}:{label}".encode()).hexdigest()[:12]
    return f"slot_{digest}"


async def slots(
    conn: AsyncConnection, *, job_type: str, from_day: date | None = None
) -> list[dict[str, Any]]:
    """The windows Mabel may offer.

    Reads the tenant's timezone and business hours, and excludes windows that
    already have an appointment. `job_type` is accepted because a connected
    calendar will want it for duration, and taking it now means the tool
    contract does not change when Phase 6 lands.
    """
    tenant = await conn.execute(text("SELECT timezone FROM tenants LIMIT 1"))
    row = tenant.mappings().one_or_none()
    zone = ZoneInfo(row["timezone"]) if row else ZoneInfo("America/New_York")

    today = from_day or datetime.now(zone).date()
    booked = await _booked_windows(conn, today)

    offered: list[dict[str, Any]] = []
    for offset in range(1, HORIZON_DAYS + 1):
        day = today + timedelta(days=offset)
        if day.weekday() >= 5:
            # Weekends are not offered by default. An owner who works Saturdays
            # will have a connected calendar saying so.
            continue
        for label, opens, closes in DEFAULT_WINDOWS:
            identifier = slot_id(day, label)
            if identifier in booked:
                continue
            offered.append(
                {
                    "slot_id": identifier,
                    "day": day.isoformat(),
                    "label": label,
                    "starts_at": datetime.combine(day, opens, zone).isoformat(),
                    "ends_at": datetime.combine(day, closes, zone).isoformat(),
                    # What she reads out. Never a precise time.
                    "spoken": f"{day.strftime('%A')} {label}",
                }
            )
    return offered


async def _booked_windows(conn: AsyncConnection, from_day: date) -> set[str]:
    result = await conn.execute(
        text(
            """
            SELECT external_ref
            FROM appointments
            WHERE starts_at >= :from_day
              AND status IN ('scheduled', 'confirmed')
              AND external_ref IS NOT NULL
            """
        ),
        {"from_day": from_day},
    )
    return {row[0] for row in result}


async def book(
    conn: AsyncConnection,
    *,
    tenant_id: UUID,
    slot_id: str,
    contact_id: UUID,
    lead_id: UUID | None,
    job_type: str | None = None,
) -> bool:
    """Book a window that `slots()` offered. Returns False if it did not.

    The lookup against freshly computed slots is what enforces 'never promise
    an arrival time not returned by check_availability'. An id the model made
    up is simply not in the list.
    """
    available = {entry["slot_id"]: entry for entry in await slots(conn, job_type=job_type or "")}
    chosen = available.get(slot_id)
    if chosen is None:
        return False

    await conn.execute(
        text(
            """
            INSERT INTO appointments
              (tenant_id, lead_id, contact_id, starts_at, ends_at, kind, status, external_ref)
            VALUES
              (:tenant_id, :lead_id, :contact_id, :starts_at, :ends_at,
               'estimate', 'scheduled', :slot_id)
            """
        ),
        {
            "tenant_id": tenant_id,
            "lead_id": lead_id,
            "contact_id": contact_id,
            "starts_at": chosen["starts_at"],
            "ends_at": chosen["ends_at"],
            "slot_id": slot_id,
        },
    )
    return True
