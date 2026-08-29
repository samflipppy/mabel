"""The owner's SMS interface.

The grammar is the reason `WON RUIZ 3800` does not go anywhere near a model:
it is the one place a job value enters the system, and invariant 4 says no LLM
output becomes a dollar figure.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mabel_domain.money import Money
from mabel_sms.compose import (
    SEGMENT,
    RecapLead,
    fit,
    followup_nudge,
    followups,
    help_message,
    lead_detail,
    lost_confirmation,
    morning_recap,
    silence_alert,
    stop_confirmation,
    to_gsm7,
    weekly_summary,
    won_confirmation,
)
from mabel_sms.intents import Intent, is_carrier_keyword, parse
from mabel_sms.recall import (
    MONEY_KEYS,
    RecallRefused,
    RecallRow,
    build_prompt,
    no_records_reply,
    safe_answer,
    strip_money,
    to_rows,
    validate_answer,
)

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


class TestTheGrammar:
    @pytest.mark.parametrize(("raw", "index"), [("1", 1), ("2", 2), ("9", 9)])
    def test_a_bare_digit_expands_a_list_item(self, raw, index):
        parsed = parse(raw)
        assert parsed.intent is Intent.EXPAND
        assert parsed.index == index

    def test_fu_opens_followups(self):
        assert parse("FU").intent is Intent.FOLLOWUPS
        assert parse("fu").intent is Intent.FOLLOWUPS

    def test_c_bridges_a_call(self):
        assert parse("C").intent is Intent.BRIDGE_CALL

    def test_a_bare_surname_asks_for_a_thread(self):
        parsed = parse("HENDERSON")
        assert parsed.intent is Intent.CONTACT_SUMMARY
        assert parsed.subject == "HENDERSON"

    def test_a_sentence_falls_through_to_recall(self):
        parsed = parse("did that guy from detroit ave ever call back")
        assert parsed.intent is Intent.RECALL

    def test_an_empty_message_does_not_crash(self):
        assert parse("").intent is Intent.RECALL
        assert parse("   ").intent is Intent.RECALL


class TestWon:
    """The one place a dollar figure enters the system."""

    def test_the_canonical_form(self):
        parsed = parse("WON RUIZ 3800")
        assert parsed.intent is Intent.MARK_WON
        assert parsed.subject == "RUIZ"
        assert parsed.amount == Money(380_000)

    def test_lowercase_works(self):
        assert parse("won ruiz 3800").amount == Money(380_000)

    def test_a_dollar_sign_is_accepted(self):
        assert parse("WON RUIZ $3800").amount == Money(380_000)

    def test_a_thousands_separator_is_accepted(self):
        assert parse("WON RUIZ 3,800").amount == Money(380_000)

    def test_cents_are_accepted(self):
        assert parse("WON RUIZ 3800.50").amount == Money(380_050)

    def test_no_amount_is_a_valid_command(self):
        """He may not know the value yet. The handler asks for it."""
        parsed = parse("WON RUIZ")
        assert parsed.intent is Intent.MARK_WON
        assert parsed.amount is None
        assert parsed.note == "no amount given"

    def test_a_two_word_name_is_not_read_as_an_amount(self):
        parsed = parse("WON MARY BETH")
        assert parsed.intent is Intent.MARK_WON
        assert parsed.subject == "MARY BETH"
        assert parsed.amount is None

    def test_a_typoed_amount_is_never_guessed_at(self):
        """A misparsed figure here becomes the headline number on the monthly
        report. Ask him again rather than take the closest reading."""
        parsed = parse("WON RUIZ 38OO")  # letter O, not zero
        assert parsed.amount is None
        assert parsed.note is not None

    def test_the_amount_is_always_integer_cents(self):
        amount = parse("WON RUIZ 3800").amount
        assert amount is not None
        assert isinstance(amount.cents, int)

    def test_an_absurd_amount_is_refused(self):
        parsed = parse("WON RUIZ 999999999")
        assert parsed.amount is None


class TestLost:
    def test_the_canonical_form(self):
        parsed = parse("LOST CHEN")
        assert parsed.intent is Intent.MARK_LOST
        assert parsed.subject == "CHEN"

    def test_a_reason_is_captured(self):
        parsed = parse("LOST CHEN went with someone else")
        assert parsed.intent is Intent.MARK_LOST
        assert parsed.meta["reason"] == "went with someone else"


class TestCarrierKeywords:
    @pytest.mark.parametrize("word", ["STOP", "stop", "UNSUBSCRIBE", "CANCEL", "QUIT", "END"])
    def test_stop_always_wins(self, word):
        """Not ours to reinterpret. Replying to STOP with anything but
        compliance is an A2P violation."""
        assert parse(word).intent is Intent.STOP

    @pytest.mark.parametrize("word", ["HELP", "help", "INFO", "?"])
    def test_help_is_recognised(self, word):
        assert parse(word).intent is Intent.HELP

    def test_they_are_detectable_before_tenant_resolution(self):
        # An unsubscribe from a number we cannot place is still an unsubscribe.
        assert is_carrier_keyword("STOP") is True
        assert is_carrier_keyword("WON RUIZ 3800") is False

    def test_stop_is_not_read_as_a_name(self):
        assert parse("STOP").intent is not Intent.CONTACT_SUMMARY


class TestGsm7:
    def test_curly_quotes_and_dashes_are_substituted(self):
        assert to_gsm7("Ray’s — job") == "Ray's - job"

    def test_emoji_are_stripped(self):
        """One character outside GSM-7 switches the whole message to UCS-2 and
        halves the segment length. That is how a 158-character message becomes
        three parts that arrive out of order."""
        assert "\U0001f600" not in to_gsm7("done \U0001f600")

    def test_accented_characters_that_are_in_gsm7_survive(self):
        # é and ß are both in the alphabet. Stripping a customer's name would
        # be worse than the problem.
        assert to_gsm7("café straße") == "café straße"

    def test_fit_stays_within_one_segment(self):
        assert len(fit("word " * 100)) <= SEGMENT

    def test_fit_breaks_on_a_word_boundary(self):
        trimmed = fit("Bartholomew Fitzwilliam " * 20)
        assert trimmed.endswith("...")
        assert "  " not in trimmed

    def test_fit_uses_three_dots_not_an_ellipsis_character(self):
        # U+2026 is not in GSM-7.
        assert "…" not in fit("x" * 300)

    def test_a_short_message_is_left_alone(self):
        assert fit("Marked Ruiz won.") == "Marked Ruiz won."


class TestTheMorningRecap:
    LEADS = [
        RecapLead("Pat Example", "burst pipe", "emergency", "+12165550148", NOW),
        RecapLead("Dana Ruiz", "water heater", "routine", "+12165550149", NOW),
    ]

    def test_it_leads_with_what_happened(self):
        body = morning_recap(
            business_name="Ruiz Plumbing",
            leads=self.LEADS,
            emergencies=1,
            calls_answered=4,
            local_day="Tue",
        )
        assert body.startswith("Tue: 4 calls")
        assert "1 emergency" in body

    def test_emergencies_are_marked_in_the_list(self):
        body = morning_recap(
            business_name="x", leads=self.LEADS, emergencies=1, calls_answered=4, local_day="Tue"
        )
        assert "1! Pat Example" in body

    def test_it_tells_him_how_to_act(self):
        body = morning_recap(
            business_name="x", leads=self.LEADS, emergencies=1, calls_answered=4, local_day="Tue"
        )
        assert "Reply 1-3" in body

    def test_a_quiet_night_says_so(self):
        """Silence is information. Saying nothing makes him wonder whether the
        forwarding broke."""
        body = morning_recap(
            business_name="x", leads=[], emergencies=0, calls_answered=0, local_day="Tue"
        )
        assert "no calls overnight" in body

    def test_more_than_three_leads_are_summarised(self):
        many = self.LEADS * 3
        body = morning_recap(
            business_name="x", leads=many, emergencies=0, calls_answered=6, local_day="Tue"
        )
        assert "+3 more" in body

    def test_it_is_gsm7_clean(self):
        body = morning_recap(
            business_name="Ray’s Plumbing",
            leads=self.LEADS,
            emergencies=0,
            calls_answered=2,
            local_day="Tue",
        )
        assert to_gsm7(body) == body


class TestConfirmations:
    def test_a_won_confirmation_reads_the_figure_back(self):
        """He typed it one-handed. A transposed digit is a wrong number on the
        monthly report."""
        body = won_confirmation(name="Ruiz", amount=Money(380_000))
        assert "$3,800" in body

    def test_a_won_confirmation_without_a_figure_asks_for_one(self):
        body = won_confirmation(name="Ruiz", amount=None)
        assert "value" in body.lower()

    def test_a_lost_confirmation_carries_the_reason(self):
        assert "someone else" in lost_confirmation(name="Chen", reason="went with someone else")

    def test_every_figure_comes_from_cents(self):
        # Money.format_whole is deterministic code reading an integer column.
        assert won_confirmation(name="X", amount=Money(1_460_000)).count("$") == 1
        assert "$14,600" in won_confirmation(name="X", amount=Money(1_460_000))


class TestOtherMessages:
    def test_followups_are_oldest_first_with_an_age(self):
        old = RecapLead(
            "Pat", "burst pipe", "routine", "+12165550148", datetime(2026, 10, 11, tzinfo=UTC)
        )
        body = followups([old], local_now=NOW)
        assert "3d" in body

    def test_nothing_waiting_says_so_plainly(self):
        assert "Nothing waiting" in followups([], local_now=NOW)

    def test_the_weekly_summary_totals_owner_entered_cents(self):
        body = weekly_summary(
            calls_answered=23,
            leads_created=14,
            emergencies=3,
            jobs_won=5,
            won_value_cents=1_460_000,
        )
        assert "$14,600" in body

    def test_a_bad_week_says_so(self):
        """A summary that is always good news gets ignored."""
        body = weekly_summary(
            calls_answered=2, leads_created=0, emergencies=0, jobs_won=0, won_value_cents=0
        )
        assert "No jobs marked won" in body

    def test_the_silence_alert_names_the_likely_cause(self):
        body = silence_alert(business_name="Ruiz Plumbing", days_quiet=9)
        assert "forwarding" in body
        assert "9 days" in body

    def test_a_nudge_carries_a_dialable_number(self):
        lead = RecapLead("Pat", "burst pipe", "routine", "+12165550148", NOW)
        assert "(216) 555-0148" in followup_nudge(lead, hours=26)

    def test_help_lists_the_grammar(self):
        body = help_message(business_name="Ruiz Plumbing")
        for token in ("FU", "WON", "LOST", "STOP"):
            assert token in body

    def test_stop_is_unambiguous_and_does_not_argue(self):
        body = stop_confirmation()
        assert "not receive further messages" in body
        assert len(body) <= SEGMENT

    def test_lead_detail_formats_on_every_platform(self):
        # %-I is a glibc extension; a ValueError here would take out the whole
        # recap job on Windows.
        lead = RecapLead("Pat", "burst pipe", "routine", "+12165550148", NOW, value_cents=380_000)
        body = lead_detail(lead)
        assert "Pat" in body
        assert "$3,800" in body


class TestRecallNeverProducesAFigure:
    ROWS = [
        RecallRow("call", NOW, "Pat Example", "Called about a burst pipe."),
        RecallRow("sms_out", NOW, None, "Left a message."),
    ]

    def test_money_keys_are_stripped_before_the_model_sees_a_row(self):
        cleaned = strip_money({"kind": "call", "body": "x", "value_cents": 380_000, "amount": 99})
        assert "value_cents" not in cleaned
        assert "amount" not in cleaned
        assert cleaned["body"] == "x"

    def test_every_money_key_is_covered(self):
        row = dict.fromkeys(MONEY_KEYS, 1) | {"kind": "call"}
        assert set(strip_money(row)) == {"kind"}

    def test_to_rows_drops_money_from_thread_events(self):
        rows = to_rows([{"kind": "call", "occurred_at": NOW, "body": "x", "value_cents": 380_000}])
        assert "380000" not in str(rows)

    def test_the_prompt_forbids_mentioning_money(self):
        prompt = build_prompt("did he call back", self.ROWS, business_name="Ruiz Plumbing")
        assert "Never mention an amount of money" in prompt

    def test_the_prompt_forbids_guessing(self):
        prompt = build_prompt("did he call back", self.ROWS, business_name="x")
        assert "Do not guess" in prompt

    @pytest.mark.parametrize(
        "answer",
        [
            "The job came to $3,800.",
            "He paid 3800 dollars.",
            "It was about 450 bucks.",
        ],
    )
    def test_a_figure_in_the_answer_is_refused(self, answer):
        with pytest.raises(RecallRefused):
            validate_answer(answer)

    def test_a_refused_answer_is_dropped_not_trimmed(self):
        """A sentence with the figure cut out reads like a bug, and he cannot
        tell what was removed."""
        reply = safe_answer(
            question="what was it worth", rows=self.ROWS, model_answer="It was $3,800."
        )
        assert "$" not in reply
        assert "3,800" not in reply
        assert "portal" in reply

    def test_an_ordinary_answer_passes(self):
        assert validate_answer("Yes, he called back on Tuesday.") == (
            "Yes, he called back on Tuesday."
        )

    def test_a_date_is_not_mistaken_for_money(self):
        assert validate_answer("He called on the 14th, twice.")

    def test_an_empty_answer_is_refused(self):
        with pytest.raises(RecallRefused):
            validate_answer("   ")


class TestRecallIsGrounded:
    def test_no_rows_means_no_model_call_at_all(self):
        """A model asked 'did he call back?' with no context produces a
        plausible yes, and a plausible yes is worse than an honest nothing."""
        reply = safe_answer(question="did he call back", rows=[], model_answer="Yes, on Tuesday.")
        assert "couldn't find anything" in reply

    def test_no_model_answer_falls_back_honestly(self):
        rows = [RecallRow("call", NOW, "Pat", "Called.")]
        assert "couldn't find" in safe_answer(question="x", rows=rows, model_answer=None)

    def test_the_no_records_reply_points_somewhere_useful(self):
        assert "portal" in no_records_reply("anything")

    def test_the_prompt_says_when_there_are_no_records(self):
        assert "(no matching records)" in build_prompt("x", [], business_name="y")

    def test_every_reply_fits_one_segment(self):
        rows = [RecallRow("call", NOW, "Pat", "x")]
        for reply in (
            no_records_reply("x"),
            safe_answer(question="x", rows=rows, model_answer="It was $99."),
            safe_answer(question="x", rows=[], model_answer=None),
        ):
            assert len(reply) <= SEGMENT
