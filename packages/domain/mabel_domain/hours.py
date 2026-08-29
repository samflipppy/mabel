"""After-hours, computed. Never stored.

Invariant 6: timestamps are `timestamptz` in UTC, every tenant carries an IANA
timezone, and "is it after hours" is derived at the moment you ask. Storing it
would mean a row that was right when it was written and wrong an hour later.

Pure. Takes an instant and a config, returns a decision.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from mabel_domain.models import BusinessHours


def to_tenant_local(instant: datetime, timezone: str) -> datetime:
    """UTC instant to tenant wall-clock. Raises on a naive datetime — a naive
    timestamp in this system is a bug, not a convenience."""
    if instant.tzinfo is None:
        raise ValueError("naive datetime: every instant in Mabel is timezone-aware UTC")
    return instant.astimezone(ZoneInfo(timezone))


def is_open(hours: BusinessHours, instant: datetime, timezone: str) -> bool:
    """Is the shop open at this instant, by its own clock?

    A day with no entry is closed. A close time earlier than its open time
    means the day runs past midnight (a 24-hour towing outfit configured
    `08:00`–`07:00`, say), and we treat the window as wrapping rather than as
    an empty interval.
    """
    local = to_tenant_local(instant, timezone)
    today = hours.for_weekday(local.weekday())
    now = local.time()

    if today is not None and _within(today.open, today.close, now):
        return True

    # A window that wraps midnight is still open in the small hours of the
    # *following* day, so check yesterday's window too.
    yesterday = hours.for_weekday((local.weekday() - 1) % 7)
    return yesterday is not None and yesterday.close <= yesterday.open and now < yesterday.close


def is_after_hours(hours: BusinessHours, instant: datetime, timezone: str) -> bool:
    return not is_open(hours, instant, timezone)


def _within(open_at: time, close_at: time, now: time) -> bool:
    if open_at < close_at:
        return open_at <= now < close_at
    if open_at == close_at:
        # Equal times mean open around the clock. Nobody configures a
        # zero-length day on purpose, and reading it as "closed" would send a
        # 24-hour shop's calls to voicemail.
        return True
    # Wraps midnight: open until the end of the day.
    return now >= open_at


def in_quiet_hours(start: time, end: time, instant: datetime, timezone: str) -> bool:
    """The "never text me between 1am and 5am" override from the portal.

    Almost always wraps midnight, which is why it gets the same wrapping
    treatment as business hours rather than a naive `start <= now < end`.
    """
    now = to_tenant_local(instant, timezone).time()
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end
