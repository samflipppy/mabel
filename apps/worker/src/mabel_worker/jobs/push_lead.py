"""Push a lead to every connected integration.

Enqueued after the lead is committed, never during. 04-REPO.md's Phase 6 bar is
"a lead lands in a customer's Jobber without anyone touching it", and the
important half of that is *without losing it if Jobber is down*.

Three rules, and they follow from that:

**One integration failing never affects another.** Each is attempted
separately. A Jobber outage must not stop the outbound webhook, because the
contractor may be relying on either.

**Every attempt is recorded.** `integration_events` gets a row whether it
worked or not. An integration that silently stops working is worse than one
that visibly fails: the contractor keeps believing his leads are arriving.

**A failure marks the integration, not the lead.** The lead is already safe in
Mabel. What gets `status='error'` is `integrations`, which is what the
Integrations screen reads.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from mabel_db.tenant import tenant_scope
from mabel_integrations.base import (
    IntegrationUnavailable,
    LeadPayload,
    Provider,
    PushResult,
    read_credentials,
)
from mabel_integrations.google_calendar import GoogleCalendar
from mabel_integrations.housecall import HousecallPro
from mabel_integrations.jobber import Jobber
from mabel_integrations.outbound_webhook import OutboundWebhook
from mabel_worker.queue import Job

logger = logging.getLogger(__name__)

BUILDERS = {
    Provider.GOOGLE_CALENDAR: GoogleCalendar,
    Provider.JOBBER: Jobber,
    Provider.HOUSECALL_PRO: HousecallPro,
    Provider.WEBHOOK: OutboundWebhook,
}


async def run(job: Job, engine: AsyncEngine) -> None:
    if job.tenant_id is None:
        raise ValueError("push_lead needs a tenant")

    raw_lead_id = job.payload.get("lead_id")
    if not raw_lead_id:
        raise ValueError("push_lead needs a lead_id in its payload")
    lead_id = UUID(str(raw_lead_id))

    async with tenant_scope(job.tenant_id, engine=engine) as conn:
        payload = await _load_lead(conn, lead_id)
        if payload is None:
            # Deleted between enqueue and here. Not an error.
            return

        connected = await _connected_integrations(conn)
        for row in connected:
            await _push_one(conn, job.tenant_id, row, payload)


async def _push_one(
    conn: AsyncConnection, tenant_id: UUID, row: dict[str, Any], payload: LeadPayload
) -> None:
    """Attempt one integration. Never raises past this function.

    A raise here would abandon the remaining integrations, which is the thing
    the whole module is arranged to avoid.
    """
    provider = Provider(row["provider"])
    builder = BUILDERS.get(provider)
    if builder is None:
        return

    try:
        credentials = await read_credentials(row["vault_key"])
    except IntegrationUnavailable as exc:
        # No token. Recorded as a failure with the reason rather than skipped
        # silently, so the Integrations screen can say why.
        await _record(conn, tenant_id, row["id"], PushResult(ok=False, error=str(exc)), payload)
        await _mark_status(conn, row["id"], "expired", str(exc))
        return

    client = builder()
    try:
        result = await client.push_lead(payload, credentials)
    except Exception as exc:  # noqa: BLE001 - one provider must not stop the rest
        logger.exception("push to %s failed", provider)
        result = PushResult(ok=False, error=f"{type(exc).__name__}: {exc}"[:300])
    finally:
        await client.aclose()

    await _record(conn, tenant_id, row["id"], result, payload)
    await _mark_status(
        conn,
        row["id"],
        "connected" if result.ok else "error",
        None if result.ok else result.error,
    )


async def _load_lead(conn: AsyncConnection, lead_id: UUID) -> LeadPayload | None:
    """Read the lead. Note what is not selected: `value_cents`.

    It is owner-entered, it lives in Mabel, and it is not ours to push into
    somebody else's system as though it were an estimate. Leaving it out of the
    query means it cannot reach a payload by accident.
    """
    result = await conn.execute(
        text(
            """
            SELECT id, caller_name, callback_e164, service_address, job_type,
                   description, urgency, source, created_at
            FROM leads WHERE id = :id
            """
        ),
        {"id": lead_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None

    return LeadPayload(
        lead_id=row["id"],
        caller_name=row["caller_name"],
        phone_e164=row["callback_e164"],
        address=row["service_address"],
        job_type=row["job_type"],
        description=row["description"],
        urgency=row["urgency"],
        source=row["source"],
        created_at=row["created_at"],
    )


async def _connected_integrations(conn: AsyncConnection) -> list[dict[str, Any]]:
    result = await conn.execute(
        text(
            "SELECT id, provider, vault_key, config FROM integrations "
            "WHERE status IN ('connected', 'error')"
        )
    )
    # `error` is included deliberately: an integration that failed last time
    # should be retried on the next lead rather than staying broken until
    # somebody notices and reconnects it.
    return [dict(row) for row in result.mappings()]


async def _record(
    conn: AsyncConnection,
    tenant_id: UUID,
    integration_id: UUID,
    result: PushResult,
    payload: LeadPayload,
) -> None:
    await conn.execute(
        text(
            """
            INSERT INTO integration_events
              (tenant_id, integration_id, direction, entity, external_ref, status, payload)
            VALUES
              (:tenant_id, :integration_id, 'outbound', 'lead', :ref, :status,
               cast(:payload as jsonb))
            """
        ),
        {
            "tenant_id": tenant_id,
            "integration_id": integration_id,
            "ref": result.external_ref,
            "status": result.status,
            # `PushResult.payload` is already redacted by the integration.
            "payload": json.dumps(
                {"lead_id": str(payload.lead_id), "error": result.error, **result.payload},
                default=str,
            ),
        },
    )


async def _mark_status(
    conn: AsyncConnection, integration_id: UUID, status: str, error: str | None
) -> None:
    await conn.execute(
        text(
            "UPDATE integrations SET status = :status, last_error = :error, "
            "last_synced_at = CASE WHEN :status = 'connected' THEN now() ELSE last_synced_at END "
            "WHERE id = :id"
        ),
        {"id": integration_id, "status": status, "error": (error or "")[:500] or None},
    )
