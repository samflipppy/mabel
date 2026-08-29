"""Routing an inbound owner SMS to an action.

The grammar in `packages/sms/intents.py` says what he meant. This says what to
do about it, against his tenant's data. Split that way because the grammar is
pure and heavily fixtured, while this needs a database and is mostly
resolution: which lead is "RUIZ", what was item `1` in the list he is replying
to.

**The tenant comes from the sending number, not from the message.** An inbound
SMS is looked up against `users.phone_e164`, the same way a call is looked up
against a DID. Nothing in the message body influences which tenant's rows are
touched.

**STOP is honoured before anything else.** Before tenant resolution, before
parsing, before any database work. An unsubscribe from a number we cannot place
is still an unsubscribe, and getting that wrong is an A2P violation rather than
a bug.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from mabel_domain.money import Money
from mabel_sms.compose import (
    RecapLead,
    followups,
    help_message,
    lead_detail,
    lost_confirmation,
    stop_confirmation,
    won_confirmation,
)
from mabel_sms.compose import fit as fit_sms
from mabel_sms.intents import Intent, ParsedCommand, parse
from mabel_sms.recall import no_records_reply, safe_answer, to_rows
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Sender:
    """Who texted us. Resolved from the number, never from the body."""

    tenant_id: UUID
    user_id: UUID
    phone_e164: str


@dataclass(frozen=True, slots=True)
class Reply:
    body: str
    # Set when the command changed something, so the caller can audit it.
    action: str | None = None
    lead_id: UUID | None = None


async def resolve_sender(conn: AsyncConnection, phone_e164: str) -> Sender | None:
    """Which tenant does this number belong to?

    Runs through `admin_scope`, because there is no tenant context yet — that
    is what is being resolved. `users` is RLS-protected, so this uses the same
    approach as DID resolution: a narrow lookup, and everything afterwards
    happens inside `tenant_scope`.

    A number matching two tenants returns nothing. That is a real possibility
    — an office manager who works for two contractors — and guessing which one
    he meant would put a lead in the wrong business.
    """
    result = await conn.execute(
        text("SELECT tenant_id, user_id, phone_e164 FROM resolve_user_by_phone(:phone)"),
        {"phone": phone_e164},
    )
    rows = list(result.mappings())
    if len(rows) != 1:
        if len(rows) > 1:
            logger.warning("phone %s matches %s tenants; refusing to guess", phone_e164, len(rows))
        return None
    row = rows[0]
    return Sender(tenant_id=row["tenant_id"], user_id=row["user_id"], phone_e164=row["phone_e164"])


async def handle(
    conn: AsyncConnection, sender: Sender, message: str, *, now: datetime | None = None
) -> Reply:
    """Act on one inbound message. `conn` is already inside `tenant_scope`."""
    command = parse(message)
    moment = now or datetime.now(UTC)

    handlers = {
        Intent.STOP: _stop,
        Intent.HELP: _help,
        Intent.EXPAND: _expand,
        Intent.MARK_WON: _mark_won,
        Intent.MARK_LOST: _mark_lost,
        Intent.CONTACT_SUMMARY: _contact_summary,
        Intent.FOLLOWUPS: _followups,
        Intent.BRIDGE_CALL: _bridge_call,
        Intent.RECALL: _recall,
    }
    return await handlers[command.intent](conn, sender, command, moment)


async def _stop(conn, sender, command, now) -> Reply:
    """Opt out. Every notification preference off, and the reply is compliance
    only — no attempt to talk him out of it."""
    await conn.execute(
        text("UPDATE users SET notify_emergencies = false, notify_recap = false WHERE id = :id"),
        {"id": sender.user_id},
    )
    return Reply(body=stop_confirmation(), action="opted_out")


async def _help(conn, sender, command, now) -> Reply:
    del conn, now
    return Reply(body=help_message(business_name=""))


async def _expand(conn, sender, command, now) -> Reply:
    """`1` — expand item N from the last list he was shown.

    The list lives in `sms_sessions`, keyed by his phone number, 24-hour TTL.
    An expired session means the recap he is replying to is from days ago, and
    guessing which list he means would show him the wrong lead.
    """
    del now
    session = await _session(conn, sender.phone_e164)
    shown = (session or {}).get("last_list") or []
    index = (command.index or 1) - 1

    if not shown:
        return Reply(body=fit_sms("That list has expired. Reply FU for what's still waiting."))
    if index >= len(shown):
        return Reply(body=fit_sms(f"There were only {len(shown)} on that list."))

    item = shown[index]
    return Reply(
        body=lead_detail(
            RecapLead(
                name=item.get("name"),
                job_type=item.get("job_type"),
                urgency="routine",
                phone_e164=item.get("phone"),
                at=datetime.fromisoformat(item["at"]),
            )
        )
    )


async def _mark_won(conn, sender, command, now) -> Reply:
    """`WON RUIZ 3800`. The one write of `leads.value_cents`.

    The amount was parsed by deterministic code from digits a human typed. It
    arrives here as `Money`, which cannot hold a float, and is written as
    integer cents.
    """
    lead = await _find_lead(conn, command.subject or "")
    if lead is None:
        return Reply(body=_not_found(command.subject))

    await conn.execute(
        text(
            """
            UPDATE leads
            SET status = 'won',
                won_at = coalesce(won_at, CAST(:now AS timestamptz)),
                first_touched_at = coalesce(first_touched_at, CAST(:now AS timestamptz)),
                value_cents = coalesce(CAST(:value AS bigint), value_cents),
                updated_at = now()
            WHERE id = :id
            """
        ),
        {
            "id": lead["id"],
            "now": now,
            "value": command.amount.cents if command.amount else None,
        },
    )
    await _record(conn, sender, lead["id"], f"Marked won by SMS: {command.raw}", now)

    return Reply(
        body=won_confirmation(name=lead["caller_name"] or "that lead", amount=command.amount),
        action="marked_won",
        lead_id=lead["id"],
    )


async def _mark_lost(conn, sender, command, now) -> Reply:
    lead = await _find_lead(conn, command.subject or "")
    if lead is None:
        return Reply(body=_not_found(command.subject))

    reason = command.meta.get("reason")
    await conn.execute(
        text(
            """
            UPDATE leads
            SET status = 'lost',
                lost_reason = :reason,
                first_touched_at = coalesce(first_touched_at, CAST(:now AS timestamptz)),
                updated_at = now()
            WHERE id = :id
            """
        ),
        {"id": lead["id"], "reason": reason, "now": now},
    )
    await _record(conn, sender, lead["id"], f"Marked lost by SMS: {command.raw}", now)

    return Reply(
        body=lost_confirmation(name=lead["caller_name"] or "that lead", reason=reason),
        action="marked_lost",
        lead_id=lead["id"],
    )


async def _contact_summary(conn, sender, command, now) -> Reply:
    """A bare surname. Ambiguous by nature, so if it matches nothing we fall
    through to recall rather than telling him his own customer does not
    exist."""
    lead = await _find_lead(conn, command.subject or "", include_closed=True)
    if lead is None:
        return await _recall(conn, sender, command, now)

    return Reply(
        body=lead_detail(
            RecapLead(
                name=lead["caller_name"],
                job_type=lead["job_type"],
                urgency=lead["urgency"],
                phone_e164=lead["callback_e164"],
                at=lead["created_at"],
                value_cents=lead["value_cents"],
            )
        )
    )


async def _followups(conn, sender, command, now) -> Reply:
    del command
    result = await conn.execute(
        text(
            """
            SELECT caller_name, job_type, urgency, callback_e164, created_at
            FROM leads
            WHERE first_touched_at IS NULL AND status = 'new'
            ORDER BY created_at
            LIMIT 10
            """
        )
    )
    waiting = [
        RecapLead(
            name=row["caller_name"],
            job_type=row["job_type"],
            urgency=row["urgency"],
            phone_e164=row["callback_e164"],
            at=row["created_at"],
        )
        for row in result.mappings()
    ]
    await _remember(conn, sender, waiting, kind="followups")
    return Reply(body=followups(waiting, local_now=now))


async def _bridge_call(conn, sender, command, now) -> Reply:
    """`C` — call back the last emergency caller.

    Returns the number rather than dialling. Placing a call is an outward
    action with a cost and a consequence, and 'nothing irreversible happens
    without a human' covers dialling somebody at 3am on a one-letter text that
    could have been a fat finger.
    """
    del command, now
    result = await conn.execute(
        text(
            """
            SELECT caller_name, callback_e164
            FROM leads
            WHERE urgency = 'emergency' AND callback_e164 IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
    )
    row = result.mappings().one_or_none()
    if row is None:
        return Reply(body="No emergency callers on record.")

    from mabel_domain.phone import format_national

    return Reply(
        body=fit_sms(
            f"Last emergency: {row['caller_name'] or 'unknown'} "
            f"{format_national(row['callback_e164'])}. Tap to call."
        )
    )


async def _recall(conn, sender, command, now) -> Reply:
    """Anything the grammar did not recognise.

    Retrieval happens here, inside `tenant_scope`, so the rows can only be his.
    Composition is `packages/sms/recall.py`, which strips money going in and
    refuses figures coming out. This function does not call a model — the
    caller supplies one, so the whole path stays testable without one.
    """
    del now
    result = await conn.execute(
        text(
            """
            SELECT e.kind, e.occurred_at, e.body, e.direction, c.display_name AS who
            FROM communication_events e
            LEFT JOIN contacts c ON c.id = e.contact_id
            WHERE e.body IS NOT NULL
              AND to_tsvector('english', coalesce(e.body, ''))
                  @@ plainto_tsquery('english', :q)
            ORDER BY e.occurred_at DESC
            LIMIT 12
            """
        ),
        {"q": command.raw},
    )
    rows = to_rows([dict(row) for row in result.mappings()])
    if not rows:
        return Reply(body=no_records_reply(command.raw))

    # No model is wired up yet. Passing None means the grounded fallback is
    # returned rather than anything invented, which is the correct behaviour
    # and not a placeholder.
    return Reply(body=safe_answer(question=command.raw, rows=rows, model_answer=None))


async def _find_lead(
    conn: AsyncConnection, subject: str, *, include_closed: bool = False
) -> dict[str, Any] | None:
    """Resolve `RUIZ` to a lead.

    Most recent match wins, and a name matching nothing returns None rather
    than the closest thing. Marking the wrong job won puts a wrong number on
    the monthly report.
    """
    if not subject.strip():
        return None

    status_clause = "" if include_closed else "AND status NOT IN ('won', 'lost', 'spam')"
    result = await conn.execute(
        text(
            f"""
            SELECT id, caller_name, job_type, urgency, callback_e164, created_at, value_cents
            FROM leads
            WHERE caller_name ILIKE :pattern
              {status_clause}
            ORDER BY created_at DESC
            LIMIT 1
            """  # noqa: S608 - status_clause is a literal chosen above, never user input
        ),
        {"pattern": f"%{subject.strip()}%"},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


def _not_found(subject: str | None) -> str:
    return fit_sms(f"No open lead matching '{subject or ''}'. Reply FU for what's waiting.")


async def _record(
    conn: AsyncConnection, sender: Sender, lead_id: UUID, body: str, now: datetime
) -> None:
    """Every SMS command that changes something lands in the thread.

    The office manager looking at a lead marked won needs to see who did it and
    how, not just that it happened.
    """
    from mabel_db.queries import events as events_q

    await events_q.append(
        conn,
        tenant_id=sender.tenant_id,
        lead_id=lead_id,
        kind="status_change",
        direction="internal",
        occurred_at=now,
        body=body,
        actor_user_id=sender.user_id,
        payload={"channel": "sms", "from": sender.phone_e164},
    )


async def _session(conn: AsyncConnection, phone_e164: str) -> dict[str, Any] | None:
    result = await conn.execute(
        text("SELECT context FROM sms_sessions WHERE phone_e164 = :phone AND expires_at > now()"),
        {"phone": phone_e164},
    )
    row = result.scalar_one_or_none()
    return dict(row) if row else None


async def _remember(
    conn: AsyncConnection, sender: Sender, leads: list[RecapLead], *, kind: str
) -> None:
    """Store the list just shown, so a following `1` means something."""
    import json

    shown = [
        {
            "name": lead.name,
            "job_type": lead.job_type,
            "phone": lead.phone_e164,
            "at": lead.at.isoformat(),
        }
        for lead in leads[:3]
    ]
    await conn.execute(
        text(
            """
            INSERT INTO sms_sessions (tenant_id, user_id, phone_e164, context, expires_at)
            VALUES (:tenant_id, :user_id, :phone, cast(:context as jsonb),
                    now() + interval '24 hours')
            ON CONFLICT (phone_e164) DO UPDATE
              SET context = excluded.context,
                  expires_at = excluded.expires_at,
                  updated_at = now()
            """
        ),
        {
            "tenant_id": sender.tenant_id,
            "user_id": sender.user_id,
            "phone": sender.phone_e164,
            "context": json.dumps({"last_list": shown, "kind": kind}),
        },
    )


def money_from(command: ParsedCommand) -> Money | None:
    """Exposed for the audit log. Always integer cents or nothing."""
    return command.amount
