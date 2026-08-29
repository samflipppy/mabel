"""Google Calendar. Read availability, write estimate appointments.

This is the integration that makes `check_availability` return real slots
rather than the configured default windows. 03-VOICE.md: "Mabel never invents a
time" — with a calendar connected, the times she offers are times the
contractor is actually free.

**Free/busy, not event contents.** We ask for busy intervals through the
freeBusy API rather than reading his calendar. His dentist appointment is not
our business, and a narrower scope is easier to justify on a consent screen.

Needs a Google Cloud project that does not exist yet (docs/BLOCKED.md #10).
Until then `check_availability` falls back to the configured windows, which is
the documented behaviour rather than a degraded one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from mabel_integrations.base import (
    Credentials,
    IntegrationError,
    LeadPayload,
    Provider,
    PushResult,
    redact,
)

logger = logging.getLogger(__name__)

API_BASE = "https://www.googleapis.com/calendar/v3"
DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# Read busy times, and write only events we created. Not full calendar access:
# a narrower scope is both safer and easier to get through consent review.
SCOPES = (
    "https://www.googleapis.com/auth/calendar.freebusy",
    "https://www.googleapis.com/auth/calendar.events.owned",
)

# How far ahead we look. Matches the availability horizon.
HORIZON_DAYS = 7


@dataclass(frozen=True, slots=True)
class BusyInterval:
    start: datetime
    end: datetime

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return self.start < end and start < self.end


class GoogleCalendar:
    provider = Provider.GOOGLE_CALENDAR

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            base_url=API_BASE, timeout=DEFAULT_TIMEOUT, transport=transport
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def busy_intervals(
        self, credentials: Credentials, *, calendar_id: str = "primary"
    ) -> list[BusyInterval]:
        """When he is not free.

        Returns busy time, so an empty list means wide open. That direction
        matters: an API failure raises rather than returning an empty list,
        because "no busy time" and "we could not ask" must not look the same —
        the first offers every slot and the second should offer none.
        """
        now = datetime.now(UTC)
        response = await self._client.post(
            "/freeBusy",
            headers={"Authorization": f"Bearer {credentials.access_token}"},
            json={
                "timeMin": now.isoformat(),
                "timeMax": (now + timedelta(days=HORIZON_DAYS)).isoformat(),
                "items": [{"id": calendar_id}],
            },
        )
        if response.status_code >= 400:
            raise IntegrationError(f"google freeBusy returned {response.status_code}")

        calendars = response.json().get("calendars", {})
        busy = calendars.get(calendar_id, {}).get("busy", [])
        return [
            BusyInterval(
                start=datetime.fromisoformat(entry["start"].replace("Z", "+00:00")),
                end=datetime.fromisoformat(entry["end"].replace("Z", "+00:00")),
            )
            for entry in busy
        ]

    async def push_lead(self, lead: LeadPayload, credentials: Credentials) -> PushResult:
        """Google Calendar does not take leads — it takes appointments.

        Returning a no-op rather than raising keeps the fan-out loop in the
        worker simple: every connected integration is asked, and the ones that
        have nothing to do say so.
        """
        del lead, credentials
        return PushResult(ok=True, payload={"skipped": "calendar takes appointments, not leads"})

    async def create_appointment(
        self,
        credentials: Credentials,
        *,
        starts_at: datetime,
        ends_at: datetime,
        lead: LeadPayload,
        calendar_id: str = "primary",
    ) -> PushResult:
        """Write an estimate onto his calendar.

        The description carries the caller's details and nothing about money.
        A job value in a calendar entry is a figure that then appears on his
        phone's lock screen, which is not where an owner-entered estimate
        belongs.
        """
        body: dict[str, Any] = {
            "summary": f"Estimate: {lead.caller_name or 'after-hours caller'}",
            "description": "\n".join(
                filter(
                    None,
                    [
                        lead.job_type,
                        lead.description,
                        f"Phone: {lead.phone_e164}" if lead.phone_e164 else None,
                        "Booked by Mabel.",
                    ],
                )
            ),
            "location": lead.address,
            "start": {"dateTime": starts_at.isoformat()},
            "end": {"dateTime": ends_at.isoformat()},
            # So we can find and update it later, and so a human looking at
            # the calendar can tell where it came from.
            "source": {"title": "Mabel", "url": "https://app.hiremabel.com"},
        }

        try:
            response = await self._client.post(
                f"/calendars/{calendar_id}/events",
                headers={"Authorization": f"Bearer {credentials.access_token}"},
                json=body,
            )
        except httpx.HTTPError as exc:
            return PushResult(ok=False, error=f"unreachable: {exc}", payload=redact(body))

        if response.status_code >= 400:
            return PushResult(
                ok=False,
                error=f"google returned {response.status_code}",
                payload=redact(body),
            )

        return PushResult(
            ok=True, external_ref=str(response.json().get("id")), payload=redact(body)
        )


def free_slots(
    busy: list[BusyInterval], candidates: list[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    """Filter candidate windows down to the ones he is actually free for.

    Pure, so the interval arithmetic — which is the part that silently goes
    wrong — is testable without a Google account.
    """
    return [
        (start, end)
        for start, end in candidates
        if not any(interval.overlaps(start, end) for interval in busy)
    ]
