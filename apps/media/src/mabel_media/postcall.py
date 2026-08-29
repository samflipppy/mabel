"""Everything that happens after hangup.

03-VOICE.md lists seven steps and they run in this order for a reason:

1. Pull the recording and transcript, write them to our storage
2. Write `calls`, `transcripts`, and a `communication_events` row
3. Resolve or create the contact
4. Finalise the lead
5. Compute cost in integer cents, update `usage_daily`
6. Enqueue notifications
7. Run QA checks, flag on `calls.qa_flags`

**Archival is step one and is not conditional.** Invariant 7: xAI's resumption
cache drops history after about thirty minutes idle and is not a store. If we
have not copied it, it is gone. So the transcript we archive is the one the
media process observed *live* — fetching it back from xAI is an enrichment
(assumption A8, unconfirmed) and never the source of truth.

**Storage failing does not lose the call.** If Supabase Storage is unavailable
the recording path is left null and the row still gets written, with the reason
recorded. A transcript in the database and no audio is a bad day; no row at all
is a call that never happened as far as the contractor is concerned.

**The emergency has already fired.** It went out mid-call from
`escalate_emergency`, inside the same transaction as the lead. Nothing here
sends it again — a duplicate 3am text is its own kind of failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from mabel_domain.phone import try_normalize_e164
from mabel_media.qa import QaInputs, assistant_text_from_turns, review
from mabel_verticals.engine import classify
from mabel_verticals.loader import load_latest
from mabel_verticals.models import Ruleset
from mabel_xai.pricing import call_cost_cents, minutes_from_seconds

logger = logging.getLogger(__name__)


class ArchiveUnavailable(RuntimeError):
    """Storage is not configured or not reachable. See docs/BLOCKED.md #2."""


@dataclass(frozen=True, slots=True)
class CallOutcome:
    """What the media process observed. Everything here was seen live, so none
    of it depends on being able to read anything back from xAI."""

    call_id: str
    tenant_id: UUID
    timezone: str
    trade: str
    from_e164: str | None
    to_e164: str
    started_at: datetime
    ended_at: datetime
    turns: list[dict[str, Any]]
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    lead_id: UUID | None = None
    contact_id: UUID | None = None
    escalated: bool = False
    booked_a_slot: bool = False
    conversation_items: int = 1
    recording_bytes: bytes | None = None
    telephony_cost_cents: int = 0
    # Facts the ruleset needs that are not in the transcript: the outdoor
    # temperature, whether there is a vulnerable occupant. "No heat" is an
    # emergency at ten degrees and a routine call in June, and the temperature
    # is something we look up rather than something the caller says. Without
    # this the backstop could never agree with a weather-gated escalation, so
    # every one of them read as an over-escalation.
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_sec(self) -> int:
        return max(0, int((self.ended_at - self.started_at).total_seconds()))


@dataclass(frozen=True, slots=True)
class Archived:
    """What finalisation produced. Returned rather than logged so the caller,
    and the tests, can assert on it."""

    duration_sec: int
    voice_cost_cents: int
    telephony_cost_cents: int
    voice_minutes: float
    outcome: str
    qa_flags: list[str]
    recording_path: str | None
    transcript_chars: int
    archived_at: datetime


def full_text(turns: list[dict[str, Any]]) -> str:
    """The searchable transcript.

    Speaker-labelled, because the portal shows it alongside the audio and
    "who said that" is most of what makes a transcript useful. This is what the
    `to_tsvector` index in 01-SCHEMA.sql runs over, so it is also what "search
    for that guy who called about the water heater" actually searches.
    """
    lines = []
    for turn in turns:
        role = str(turn.get("role", "")).lower()
        speaker = "Mabel" if role in {"assistant", "mabel"} else "Caller"
        text = str(turn.get("text", "")).strip()
        if text:
            lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


def decide_outcome(call: CallOutcome, *, classification_escalates: bool) -> str:
    """Which of the `calls.outcome` values this was.

    Ordered by specificity. A call can be several of these at once and the
    label should be the most useful one for the person reading the call log.
    """
    if call.escalated or classification_escalates:
        return "emergency"
    if call.lead_id is not None:
        return "lead"
    if call.duration_sec < 10:
        # Too short to have been anything. Not marked spam, because that is a
        # judgement and this is an observation.
        return "hangup"
    if call.contact_id is not None:
        return "existing_customer"
    return "hangup"


def ruleset_for(trade: str) -> Ruleset | None:
    try:
        return load_latest(trade)
    except FileNotFoundError:
        # A trade we have no ruleset for is a real situation — a shop is sold
        # before its ruleset is written. The call still archives; it just gets
        # no backstop classification.
        logger.info("no ruleset for trade %r; skipping the QA backstop", trade)
        return None


def build_scenario(call: CallOutcome) -> dict[str, Any]:
    """The transcript, in the shape the verticals engine reads."""
    return {
        "utterances": [
            str(turn.get("text", ""))
            for turn in call.turns
            if str(turn.get("role", "")).lower() not in {"assistant", "mabel"}
        ],
        "captured": {},
        "context": dict(call.context),
    }


def compute(call: CallOutcome, *, overrides: dict[str, Any] | None = None) -> Archived:
    """Everything post-call that does not touch the world.

    Split out from `finalize` so the arithmetic, the outcome, and the QA flags
    can be tested exhaustively without a database or a storage bucket — which
    is most of what can go wrong here.
    """
    ruleset = ruleset_for(call.trade)
    backstop_escalates = False
    if ruleset is not None:
        classification = classify(ruleset, build_scenario(call), overrides=overrides)
        backstop_escalates = classification.escalate

    voice_cost = call_cost_cents(
        duration_sec=call.duration_sec, conversation_items=call.conversation_items
    )

    flags = review(
        QaInputs(
            duration_sec=call.duration_sec,
            started_at=call.started_at,
            timezone=call.timezone,
            assistant_text=assistant_text_from_turns(call.turns),
            backstop_escalates=backstop_escalates,
            escalated=call.escalated,
            booked_a_slot=call.booked_a_slot,
        )
    )

    return Archived(
        duration_sec=call.duration_sec,
        voice_cost_cents=voice_cost,
        telephony_cost_cents=call.telephony_cost_cents,
        voice_minutes=minutes_from_seconds(call.duration_sec),
        outcome=decide_outcome(call, classification_escalates=backstop_escalates),
        qa_flags=flags,
        recording_path=None,
        transcript_chars=len(full_text(call.turns)),
        archived_at=datetime.now(UTC),
    )


def recording_path_for(call: CallOutcome) -> str:
    """Where the audio goes in the private bucket.

    Partitioned by tenant then date, so a retention sweep or a tenant deletion
    is a prefix operation rather than a scan.
    """
    day = call.started_at.astimezone(UTC).date().isoformat()
    return f"{call.tenant_id}/{day}/{call.call_id}.ulaw"


async def finalize(
    call: CallOutcome,
    *,
    storage: Any | None = None,
    engine: Any | None = None,
    overrides: dict[str, Any] | None = None,
) -> Archived:
    """Archive the call and write everything down.

    `storage` is injected so this is testable without Supabase. When it is
    absent — which is the state today, see docs/BLOCKED.md #2 — the recording
    path stays null and everything else still happens. We never drop a call
    because a bucket was unavailable.
    """
    computed = compute(call, overrides=overrides)

    recording_path: str | None = None
    if call.recording_bytes and storage is not None:
        target = recording_path_for(call)
        try:
            await storage.put(target, call.recording_bytes)
            recording_path = target
        except Exception:  # noqa: BLE001 - a lost recording must not lose the call
            logger.exception(
                "failed to archive the recording for call %s; writing the row anyway",
                call.call_id,
            )
    elif call.recording_bytes and storage is None:
        logger.warning(
            "no storage configured, so the recording for call %s was not archived. "
            "See docs/BLOCKED.md #2.",
            call.call_id,
        )

    computed = Archived(
        duration_sec=computed.duration_sec,
        voice_cost_cents=computed.voice_cost_cents,
        telephony_cost_cents=computed.telephony_cost_cents,
        voice_minutes=computed.voice_minutes,
        outcome=computed.outcome,
        qa_flags=computed.qa_flags,
        recording_path=recording_path,
        transcript_chars=computed.transcript_chars,
        archived_at=computed.archived_at,
    )

    if engine is None:
        # Nothing to write to. The caller gets the computed result, which is
        # what the simulation harness and the unit tests want.
        return computed

    await _persist(call, computed, engine=engine)
    return computed


async def _persist(call: CallOutcome, computed: Archived, *, engine: Any) -> None:
    """Write the call, the transcript, the thread row, and the usage.

    One transaction. A transcript with no call row, or usage counted for a call
    that was not recorded, are both worse than nothing.
    """
    import json

    from sqlalchemy import text

    from mabel_db.queries import events as events_q
    from mabel_db.tenant import tenant_scope

    async with tenant_scope(call.tenant_id, engine=engine) as conn:
        result = await conn.execute(
            text(
                """
                INSERT INTO calls
                  (tenant_id, contact_id, lead_id, xai_call_id, from_e164, to_e164,
                   started_at, answered_at, ended_at, duration_sec, outcome,
                   recording_path, archived_at, voice_cost_cents,
                   telephony_cost_cents, qa_flags)
                VALUES
                  (:tenant_id, :contact_id, :lead_id, :xai_call_id, :from_e164, :to_e164,
                   :started_at, :started_at, :ended_at, :duration_sec, :outcome,
                   :recording_path, :archived_at, :voice_cost_cents,
                   :telephony_cost_cents, :qa_flags)
                ON CONFLICT (xai_call_id) DO UPDATE SET
                  ended_at = excluded.ended_at,
                  duration_sec = excluded.duration_sec,
                  outcome = excluded.outcome,
                  recording_path = coalesce(excluded.recording_path, calls.recording_path),
                  archived_at = excluded.archived_at,
                  voice_cost_cents = excluded.voice_cost_cents,
                  qa_flags = excluded.qa_flags
                RETURNING id
                """
            ),
            {
                "tenant_id": call.tenant_id,
                "contact_id": call.contact_id,
                "lead_id": call.lead_id,
                "xai_call_id": call.call_id,
                "from_e164": try_normalize_e164(call.from_e164),
                "to_e164": try_normalize_e164(call.to_e164),
                "started_at": call.started_at,
                "ended_at": call.ended_at,
                "duration_sec": computed.duration_sec,
                "outcome": computed.outcome,
                "recording_path": computed.recording_path,
                "archived_at": computed.archived_at,
                "voice_cost_cents": computed.voice_cost_cents,
                "telephony_cost_cents": computed.telephony_cost_cents,
                "qa_flags": computed.qa_flags,
            },
        )
        call_row_id = result.scalar_one()

        await conn.execute(
            text(
                """
                INSERT INTO transcripts (tenant_id, call_id, turns, full_text, tool_trace)
                VALUES (:tenant_id, :call_id, cast(:turns as jsonb), :full_text,
                        cast(:tool_trace as jsonb))
                """
            ),
            {
                "tenant_id": call.tenant_id,
                "call_id": call_row_id,
                "turns": json.dumps(call.turns),
                "full_text": full_text(call.turns),
                "tool_trace": json.dumps(call.tool_trace),
            },
        )

        await events_q.append(
            conn,
            tenant_id=call.tenant_id,
            contact_id=call.contact_id,
            lead_id=call.lead_id,
            kind="call",
            direction="inbound",
            occurred_at=call.started_at,
            body=None,
            payload={
                "call_id": call.call_id,
                "duration_sec": computed.duration_sec,
                "outcome": computed.outcome,
                "qa_flags": computed.qa_flags,
            },
            storage_path=computed.recording_path,
        )

        await conn.execute(
            text(
                """
                INSERT INTO usage_daily
                  (tenant_id, day, calls_answered, voice_minutes, leads_created,
                   emergencies, cost_cents)
                VALUES
                  (:tenant_id, :day, 1, :minutes, :leads, :emergencies, :cost)
                ON CONFLICT (tenant_id, day) DO UPDATE SET
                  calls_answered = usage_daily.calls_answered + 1,
                  voice_minutes = usage_daily.voice_minutes + excluded.voice_minutes,
                  leads_created = usage_daily.leads_created + excluded.leads_created,
                  emergencies = usage_daily.emergencies + excluded.emergencies,
                  cost_cents = usage_daily.cost_cents + excluded.cost_cents
                """
            ),
            {
                "tenant_id": call.tenant_id,
                "day": call.started_at.date(),
                "minutes": computed.voice_minutes,
                "leads": 1 if call.lead_id else 0,
                "emergencies": 1 if computed.outcome == "emergency" else 0,
                "cost": computed.voice_cost_cents + computed.telephony_cost_cents,
            },
        )
