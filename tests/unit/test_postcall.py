"""Post-call: the arithmetic, the outcome, and the QA flags.

Everything here runs without a database or a storage bucket, which is most of
what can go wrong post-call. The persistence itself is covered in
`tests/isolation/`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from mabel_media.postcall import (
    Archived,
    CallOutcome,
    compute,
    decide_outcome,
    finalize,
    full_text,
    recording_path_for,
)
from mabel_media.qa import (
    LOST_CALLER_SECONDS,
    QaInputs,
    assistant_text_from_turns,
    review,
    summarise,
)

TENANT = uuid4()
STARTED = datetime(2026, 10, 14, 6, 30, tzinfo=UTC)  # 02:30 in Cleveland


def call(**overrides) -> CallOutcome:
    base = {
        "call_id": "call_abc",
        "tenant_id": TENANT,
        "timezone": "America/New_York",
        "trade": "plumbing",
        "from_e164": "+12165550148",
        "to_e164": "+12165550199",
        "started_at": STARTED,
        "ended_at": STARTED + timedelta(minutes=3),
        "turns": [
            {"role": "assistant", "text": "Thanks for calling Ruiz Plumbing."},
            {"role": "caller", "text": "My pipe burst in the basement."},
            {"role": "assistant", "text": "I'll get someone out to you. What's the address?"},
        ],
    }
    return CallOutcome(**(base | overrides))


class TestCost:
    def test_three_minutes_at_the_published_rate(self):
        result = compute(call())
        # 180s at $0.08/min is 24c, plus one conversation item.
        assert result.voice_cost_cents == 25
        assert isinstance(result.voice_cost_cents, int)

    def test_minutes_are_minutes_not_money(self):
        assert compute(call()).voice_minutes == 3.0

    def test_a_zero_length_call_costs_nothing(self):
        result = compute(call(ended_at=STARTED))
        assert result.duration_sec == 0
        assert result.voice_cost_cents == 1  # the opening disclosure still happened

    def test_telephony_cost_is_carried_through_not_computed(self):
        # Telnyx tells us what it cost. We do not estimate it.
        assert compute(call(telephony_cost_cents=7)).telephony_cost_cents == 7

    def test_no_cost_is_ever_a_float(self):
        result = compute(call(ended_at=STARTED + timedelta(seconds=97)))
        assert isinstance(result.voice_cost_cents, int)
        assert isinstance(result.telephony_cost_cents, int)


class TestTranscript:
    def test_turns_are_speaker_labelled(self):
        text = full_text(call().turns)
        assert "Mabel: Thanks for calling Ruiz Plumbing." in text
        assert "Caller: My pipe burst in the basement." in text

    def test_empty_turns_are_dropped(self):
        text = full_text([{"role": "caller", "text": "   "}, {"role": "caller", "text": "hello"}])
        assert text == "Caller: hello"

    def test_the_searchable_text_contains_what_they_said(self):
        # This is what to_tsvector indexes, so it is what "search for that guy
        # who called about the water heater" actually searches.
        text = full_text([{"role": "caller", "text": "the water heater is leaking"}])
        assert "water heater" in text


class TestOutcome:
    def test_an_escalated_call_is_an_emergency(self):
        assert decide_outcome(call(escalated=True), classification_escalates=False) == "emergency"

    def test_the_backstop_alone_is_enough_to_call_it_an_emergency(self):
        assert decide_outcome(call(), classification_escalates=True) == "emergency"

    def test_a_call_with_a_lead_is_a_lead(self):
        assert decide_outcome(call(lead_id=uuid4()), classification_escalates=False) == "lead"

    def test_a_very_short_call_is_a_hangup(self):
        short = call(ended_at=STARTED + timedelta(seconds=4))
        assert decide_outcome(short, classification_escalates=False) == "hangup"

    def test_a_short_call_is_not_labelled_spam(self):
        """Spam is a judgement. This is an observation, and mislabelling a
        customer's dropped call as spam hides it from the owner."""
        short = call(ended_at=STARTED + timedelta(seconds=4))
        assert decide_outcome(short, classification_escalates=False) != "spam"

    def test_a_known_contact_with_no_lead_is_an_existing_customer(self):
        known = call(contact_id=uuid4())
        assert decide_outcome(known, classification_escalates=False) == "existing_customer"


class TestQaChecks:
    def _qa(self, **overrides) -> QaInputs:
        base = {
            "duration_sec": 180,
            "started_at": STARTED,
            "timezone": "America/New_York",
            "assistant_text": "I'll take your details and someone will call you back.",
            "backstop_escalates": False,
            "escalated": False,
            "booked_a_slot": False,
        }
        return QaInputs(**(base | overrides))

    def test_a_clean_call_has_no_flags(self):
        assert review(self._qa()) == []

    @pytest.mark.parametrize(
        "said",
        [
            "It's usually about $150 for that.",
            "That runs around 200 dollars.",
            "Somewhere around 300, give or take.",
            "The call-out is $89.",
            "Typically 250 bucks.",
        ],
    )
    def test_a_quoted_price_is_flagged(self, said):
        """Every other guard stops a price reaching her. This is the one that
        notices when one got through anyway."""
        assert "quoted_price" in review(self._qa(assistant_text=said))

    def test_the_caller_asking_about_money_is_not_her_quoting(self):
        """assistant_text_from_turns takes only her turns. Flagging the caller
        saying 'is this going to be two hundred?' trains everyone to ignore the
        flag."""
        turns = [
            {"role": "caller", "text": "Is this going to be about $200?"},
            {"role": "assistant", "text": "That depends on what's found on site."},
        ]
        assert "quoted_price" not in review(
            self._qa(assistant_text=assistant_text_from_turns(turns))
        )

    def test_a_missed_emergency_is_flagged(self):
        # The backstop caught something the model did not, and nobody's phone
        # rang. The most important flag in the file.
        flags = review(self._qa(backstop_escalates=True, escalated=False))
        assert "missed_emergency" in flags

    def test_agreeing_with_the_backstop_is_not_flagged(self):
        assert review(self._qa(backstop_escalates=True, escalated=True)) == []

    def test_escalating_a_routine_call_at_230am_is_flagged(self):
        # Waking a contractor at 2am for a slow drain is why he cancels.
        flags = review(self._qa(escalated=True, backstop_escalates=False))
        assert "over_escalated" in flags

    def test_the_same_escalation_at_2pm_is_not_flagged(self):
        afternoon = STARTED.replace(hour=18)  # 14:00 in Cleveland
        flags = review(self._qa(escalated=True, backstop_escalates=False, started_at=afternoon))
        assert "over_escalated" not in flags

    def test_a_caller_lost_in_under_twenty_seconds_is_flagged(self):
        assert "lost_caller_early" in review(self._qa(duration_sec=LOST_CALLER_SECONDS - 1))

    def test_a_promised_arrival_time_is_flagged(self):
        said = "Someone will be there at 9am tomorrow."
        assert "promised_arrival" in review(self._qa(assistant_text=said))

    def test_a_time_is_fine_when_a_slot_was_actually_booked(self):
        said = "Someone will be with you at 9am tomorrow."
        flags = review(self._qa(assistant_text=said, booked_a_slot=True))
        assert "promised_arrival" not in flags

    def test_incomplete_capture_is_only_flagged_on_a_call_that_ran(self):
        # A ten-second hangup has gaps by definition.
        short = review(self._qa(duration_sec=5, capture_gaps=("name", "address")))
        assert "capture_incomplete" not in short
        long = review(self._qa(duration_sec=200, capture_gaps=("name",)))
        assert "capture_incomplete" in long

    def test_the_summary_reads_as_a_sentence(self):
        summary = summarise(["quoted_price", "missed_emergency"])
        assert summary is not None
        assert "quoted a price" in summary

    def test_no_flags_summarises_to_nothing(self):
        assert summarise([]) is None


class TestComputeEndToEnd:
    def test_a_burst_pipe_call_where_she_did_not_escalate_is_caught(self):
        """The whole point of the backstop running post-call. She took the
        details and did not wake anyone; the ruleset says a burst pipe is a
        wake_now."""
        result = compute(call(escalated=False))
        assert "missed_emergency" in result.qa_flags
        assert result.outcome == "emergency"

    def test_the_same_call_with_an_escalation_is_clean(self):
        result = compute(call(escalated=True))
        assert result.qa_flags == []

    def test_a_muted_trigger_does_not_produce_a_missed_emergency(self):
        """The owner turned burst pipes off. Flagging her for not escalating
        something he asked her not to escalate would be nonsense."""
        result = compute(call(escalated=False), overrides={"BURST_PIPE": {"enabled": False}})
        assert "missed_emergency" not in result.qa_flags

    def test_a_trade_with_no_ruleset_still_archives(self):
        # A shop sold before its ruleset is written is a real situation.
        result = compute(call(trade="glaziers"))
        assert result.duration_sec == 180
        assert "missed_emergency" not in result.qa_flags


class TestRecordingPath:
    def test_it_is_partitioned_by_tenant_then_day(self):
        # So a retention sweep or a tenant deletion is a prefix operation
        # rather than a scan.
        path = recording_path_for(call())
        assert path == f"{TENANT}/2026-10-14/call_abc.ulaw"


class TestArchivalNeverLosesTheCall:
    async def test_no_storage_still_returns_a_result(self):
        """docs/BLOCKED.md #2: no bucket exists yet. A transcript in the
        database and no audio is a bad day. No row at all is a call that never
        happened as far as the contractor is concerned."""
        result = await finalize(call(recording_bytes=b"audio"), storage=None, engine=None)
        assert isinstance(result, Archived)
        assert result.recording_path is None
        assert result.transcript_chars > 0

    async def test_storage_failing_does_not_lose_the_call(self):
        class BrokenStorage:
            async def put(self, path, data):
                raise OSError("bucket unreachable")

        result = await finalize(
            call(recording_bytes=b"audio"), storage=BrokenStorage(), engine=None
        )
        assert result.recording_path is None
        assert result.transcript_chars > 0
        assert result.voice_cost_cents > 0

    async def test_working_storage_records_the_path(self):
        class Storage:
            def __init__(self):
                self.written = {}

            async def put(self, path, data):
                self.written[path] = data

        storage = Storage()
        result = await finalize(call(recording_bytes=b"audio"), storage=storage, engine=None)
        assert result.recording_path == f"{TENANT}/2026-10-14/call_abc.ulaw"
        assert storage.written[result.recording_path] == b"audio"

    async def test_the_transcript_we_keep_is_the_one_we_observed(self):
        """Invariant 7. xAI's cache drops history after about thirty minutes
        idle. Nothing here fetches anything back — the turns came from the live
        session."""
        result = await finalize(call(), storage=None, engine=None)
        assert result.transcript_chars == len(full_text(call().turns))
