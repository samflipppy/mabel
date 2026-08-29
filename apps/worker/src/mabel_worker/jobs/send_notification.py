"""The one job that actually sends anything.

Every other job composes a message and writes a `notifications` row. This is
what picks those up and hands them to Telnyx. Splitting it that way means a
recap that composed correctly but could not be delivered is visibly a delivery
problem, in one place, rather than each job growing its own send-and-retry.

**Failing to send is recorded as failed.** Never as sent. A recap the owner
never received but that we marked delivered is worse than one that visibly
failed, because it removes the only signal anyone would act on.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from mabel_db.tenant import tenant_scope
from mabel_telnyx.client import (
    Client,
    FakeTelnyxClient,
    SendFailed,
    TelnyxClient,
    TelnyxRefusedUnderTest,
    TelnyxUnavailable,
    delivery_risk,
)
from mabel_worker.queue import Job

logger = logging.getLogger(__name__)


def build_client() -> Client | None:
    """The live client, or nothing.

    Returns None rather than a fake when no key is configured. A fake here
    would record messages as sent that were never sent — the exact failure this
    module exists to avoid. See docs/BLOCKED.md #3.
    """
    try:
        return TelnyxClient()
    except (TelnyxUnavailable, TelnyxRefusedUnderTest):
        # Both mean the same thing here: we cannot construct a live client.
        # Returning None makes the caller record each notification as failed
        # with the reason, which is the correct unconfigured behaviour.
        #
        # Catching the under-pytest refusal as well is what makes that
        # behaviour testable end to end. Without it the guard fires first and
        # the job dies, which is a worse outcome than the one being tested and
        # would also be the outcome for any other construction failure.
        return None


async def run(job: Job, engine: AsyncEngine, *, client: Client | None = None) -> None:
    """Send every queued notification for this tenant.

    Batched per tenant rather than per message, because the cron and the other
    jobs enqueue in bursts and one claim per SMS would be mostly overhead.
    """
    if job.tenant_id is None:
        raise ValueError("send_notification needs a tenant")

    sender = client if client is not None else build_client()
    from_number = os.environ.get("TELNYX_FROM_E164")

    async with tenant_scope(job.tenant_id, engine=engine) as conn:
        pending = await _pending(conn, limit=int(job.payload.get("limit", 25)))

        for row in pending:
            if sender is None or not from_number:
                await _record_failure(
                    conn,
                    row["id"],
                    "TELNYX_API_KEY or TELNYX_FROM_E164 unset. See docs/BLOCKED.md #3.",
                )
                continue

            if row["channel"] != "sms":
                # Email is Resend and is not built yet (BLOCKED.md #9). Marked
                # failed with the reason rather than left queued forever, so
                # the queue depth stays meaningful.
                await _record_failure(conn, row["id"], f"no sender for channel {row['channel']!r}")
                continue

            try:
                sent = await sender.send_sms(
                    to_e164=row["to_address"], body=row["body"], from_e164=from_number
                )
            except SendFailed as exc:
                await _record_failure(conn, row["id"], str(exc))
                # Raised so the queue retries the whole batch with backoff. A
                # Telnyx blip should not consume the notification's only
                # chance.
                raise
            else:
                await _record_sent(conn, row["id"], sent.provider_ref)
                await _count_usage(conn, job.tenant_id, sent.segments)

    risk = delivery_risk()
    if risk == "unregistered":
        # The API accepted it and carriers may drop it. Worth a warning on
        # every send until the campaign is registered.
        logger.warning(
            "sent SMS without a registered 10DLC campaign; carriers may filter it "
            "silently. See docs/BLOCKED.md #4."
        )


async def _pending(conn: AsyncConnection, *, limit: int) -> list[dict[str, Any]]:
    result = await conn.execute(
        text(
            """
            SELECT id, channel, to_address, body, kind
            FROM notifications
            WHERE status = 'queued'
              AND (scheduled_for IS NULL OR scheduled_for <= now())
            ORDER BY
              -- An emergency goes first even if a recap was queued before it.
              CASE WHEN kind = 'emergency' THEN 0 ELSE 1 END,
              created_at
            LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    return [dict(row) for row in result.mappings()]


async def _record_sent(conn: AsyncConnection, notification_id: UUID, provider_ref: str) -> None:
    await conn.execute(
        text(
            "UPDATE notifications SET status = 'sent', sent_at = now(), "
            "provider_ref = :ref, error = NULL WHERE id = :id"
        ),
        {"id": notification_id, "ref": provider_ref},
    )


async def _record_failure(conn: AsyncConnection, notification_id: UUID, error: str) -> None:
    await conn.execute(
        text("UPDATE notifications SET status = 'failed', error = :error WHERE id = :id"),
        {"id": notification_id, "error": error[:500]},
    )


async def _count_usage(conn: AsyncConnection, tenant_id: UUID, segments: int) -> None:
    """SMS is billable. Counted here, where we know it actually went."""
    await conn.execute(
        text(
            """
            INSERT INTO usage_daily (tenant_id, day, sms_sent)
            VALUES (:tenant_id, current_date, :segments)
            ON CONFLICT (tenant_id, day) DO UPDATE
              SET sms_sent = usage_daily.sms_sent + excluded.sms_sent
            """
        ),
        {"tenant_id": tenant_id, "segments": segments},
    )


__all__ = ["FakeTelnyxClient", "build_client", "run"]
