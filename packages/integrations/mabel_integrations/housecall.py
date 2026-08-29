"""Housecall Pro. Push leads to the Job Inbox.

00-STACK.md schedules this for v2.1 and notes the constraint that matters:
**MAX plan only.** The Job Inbox API is not on their lower tiers, so a customer
on Essentials or XL cannot use this however well we implement it.

That constraint is checked and reported rather than discovered at the first
push. A contractor who connects this, sees "connected", and then never gets a
lead has a worse experience than one told up front that his plan does not
include it.

Needs an account and a MAX-plan customer to test against, neither of which
exists (docs/BLOCKED.md #12).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from mabel_integrations.base import (
    Credentials,
    LeadPayload,
    Provider,
    PushResult,
    redact,
)

logger = logging.getLogger(__name__)

API_BASE = "https://api.housecallpro.com"
DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

# The plans that include the Job Inbox API.
SUPPORTED_PLANS = frozenset({"max"})


class PlanNotSupported(RuntimeError):
    """Their Housecall Pro plan does not include the API we need."""


class HousecallPro:
    provider = Provider.HOUSECALL_PRO

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            base_url=API_BASE, timeout=DEFAULT_TIMEOUT, transport=transport
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def check_plan(self, credentials: Credentials) -> str | None:
        """Which plan they are on, so we can say so before they rely on it."""
        try:
            response = await self._client.get(
                "/company",
                headers={"Authorization": f"Bearer {credentials.access_token}"},
            )
        except httpx.HTTPError as exc:
            logger.warning("housecall plan check failed: %s", exc)
            return None

        if response.status_code >= 400:
            return None
        return str(response.json().get("plan", "")).lower() or None

    async def push_lead(self, lead: LeadPayload, credentials: Credentials) -> PushResult:
        body: dict[str, Any] = {
            "customer": {
                "first_name": (lead.caller_name or "After-hours caller").split(" ")[0],
                "last_name": " ".join((lead.caller_name or "").split(" ")[1:]),
                "mobile_number": lead.phone_e164,
            },
            "address": {"street": lead.address} if lead.address else None,
            "description": "\n".join(
                filter(None, [lead.job_type, lead.description, "Taken by Mabel."])
            ),
            # Their urgency vocabulary, not ours.
            "priority": "high" if lead.urgency == "emergency" else "normal",
            "lead_source": lead.source or "After-hours call",
        }

        try:
            response = await self._client.post(
                "/leads",
                headers={"Authorization": f"Bearer {credentials.access_token}"},
                json={k: v for k, v in body.items() if v is not None},
            )
        except httpx.HTTPError as exc:
            return PushResult(ok=False, error=f"unreachable: {exc}", payload=redact(body))

        if response.status_code == 403:
            # The plan case, specifically. A generic "403" would send somebody
            # looking for a permissions problem that does not exist.
            return PushResult(
                ok=False,
                error=(
                    "Housecall Pro rejected this. The Job Inbox API is MAX-plan "
                    "only — check the plan on their side."
                ),
                payload=redact(body),
            )

        if response.status_code >= 400:
            return PushResult(
                ok=False,
                error=f"housecall pro returned {response.status_code}",
                payload=redact(body),
            )

        return PushResult(
            ok=True, external_ref=str(response.json().get("id")), payload=redact(body)
        )
