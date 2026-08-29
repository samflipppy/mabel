"""The engine's edges, separate from the fixtures.

Fixtures say what should happen on a real call. These say what happens when
the inputs are wrong, missing, or adversarial — the cases nobody writes a
fixture for because nobody would phone them in.
"""

from __future__ import annotations

import pytest

from mabel_verticals.engine import (
    capture_gaps,
    classify,
    disagreed,
    match_phrases,
    severity_of,
)
from mabel_verticals.loader import load_latest, load_ruleset, parse_ruleset
from mabel_verticals.models import Notify, RulesetError, Severity, Urgency

PLUMBING = load_ruleset("plumbing", 3)
HVAC = load_ruleset("hvac", 2)


def scenario(*utterances: str, captured=None, context=None) -> dict:
    return {
        "utterances": list(utterances),
        "captured": captured
        if captured is not None
        else {
            "name": "Pat",
            "address": "100 Example Ave",
            "callback": "+12165550100",
            "problem": "x",
            "urgency": "x",
            "source": "google",
        },
        "context": context or {},
    }


class TestTheModelDoesNotGetToDecideSeverity:
    def test_a_known_code_gets_the_librarys_severity(self):
        # The model says SLOW_DRAIN. The library says routine. Routine it is.
        result = classify(PLUMBING, scenario("the sink is slow"), model_code="SLOW_DRAIN")
        assert result.severity is Severity.ROUTINE
        assert result.escalate is False

    def test_an_invented_code_is_not_an_emergency(self):
        """If the model returns a category we have never heard of, we do not
        get to decide it is urgent. It falls through to the ordinary lead
        path."""
        result = classify(PLUMBING, scenario("something odd"), model_code="CATASTROPHIC_VIBES")
        assert result.trigger is None
        assert result.escalate is False
        assert result.notify is Notify.RECAP_7AM
        assert result.urgency is Urgency.ROUTINE

    def test_a_code_from_another_trade_does_not_carry_over(self):
        result = classify(PLUMBING, scenario("no heat"), model_code="GAS_SMELL")
        assert result.trigger is None

    def test_a_model_code_still_has_to_satisfy_its_conditions(self):
        """The model saying NO_HEAT_FREEZING in June does not make it freezing.
        Without the temperature we do not escalate."""
        result = classify(
            HVAC,
            scenario("the furnace is out", context={"outdoor_temp_f": 70}),
            model_code="NO_HEAT_FREEZING",
        )
        assert result.trigger != "NO_HEAT_FREEZING"
        assert result.severity is Severity.MORNING


class TestTheBackstop:
    def test_it_stands_in_when_the_model_said_nothing(self):
        result = classify(PLUMBING, scenario("my pipe burst"))
        assert result.trigger == "BURST_PIPE"
        assert result.matched_by == "phrases"

    def test_the_more_severe_answer_wins(self):
        result = classify(
            PLUMBING, scenario("there's raw sewage in the basement"), model_code="SLOW_DRAIN"
        )
        assert result.trigger == "SEWAGE_BACKUP"
        assert result.matched_by == "phrases_over_model"
        assert disagreed(result) is True

    def test_the_model_wins_when_it_is_the_more_severe_one(self):
        # She heard something the phrase list does not cover. That is the case
        # the phrase list exists to lose.
        result = classify(
            PLUMBING,
            scenario("water is coming through the light fixture in the kitchen"),
            model_code="WATER_NEAR_ELECTRICAL",
        )
        assert result.trigger == "WATER_NEAR_ELECTRICAL"
        assert result.matched_by == "model"

    def test_agreement_is_recorded_as_agreement(self):
        result = classify(PLUMBING, scenario("my pipe burst"), model_code="BURST_PIPE")
        assert result.matched_by == "both"
        assert disagreed(result) is False

    def test_most_severe_wins_regardless_of_order_in_the_file(self):
        # A caller who mentions both. The order the triggers sit in the JSON
        # must not decide which one gets answered.
        result = classify(PLUMBING, scenario("the drain is clogged and there's sewage coming up"))
        assert result.trigger == "SEWAGE_BACKUP"

    def test_matching_is_bounded_to_whole_words(self):
        assert match_phrases(PLUMBING, scenario("the snow water is melting")) is None

    def test_matching_tolerates_a_plural(self):
        roofing = load_latest("roofing")
        matched = match_phrases(roofing, scenario("the gutters are overflowing"))
        assert matched is not None and matched.code == "GUTTER_ISSUE"

    def test_the_problem_field_is_searched_too(self):
        # She may have summarised it into `problem` rather than repeating it.
        result = classify(
            PLUMBING,
            scenario("hi there", captured={"problem": "pipe burst in the basement"}),
        )
        assert result.trigger == "BURST_PIPE"


class TestConditions:
    def test_a_missing_fact_does_not_escalate(self):
        """No temperature reading means we do not assume the worst. Assuming
        the worst here means waking someone at 3am on a guess."""
        result = classify(HVAC, scenario("no heat"), model_code="NO_HEAT_FREEZING")
        assert result.severity is Severity.MORNING

    def test_a_non_numeric_temperature_does_not_escalate(self):
        result = classify(
            HVAC,
            scenario("no heat", context={"outdoor_temp_f": "cold"}),
            model_code="NO_HEAT_FREEZING",
        )
        assert result.severity is Severity.MORNING

    def test_a_boolean_is_not_a_temperature(self):
        # True == 1 in Python, and 1 <= 32, so a careless check would read
        # `outdoor_temp_f: True` as one degree and wake somebody up.
        result = classify(
            HVAC,
            scenario("no heat", context={"outdoor_temp_f": True}),
            model_code="NO_HEAT_FREEZING",
        )
        assert result.severity is Severity.MORNING

    def test_the_boundary_is_inclusive(self):
        at_freezing = classify(HVAC, scenario("no heat", context={"outdoor_temp_f": 32}))
        assert at_freezing.trigger == "NO_HEAT_FREEZING"
        just_above = classify(HVAC, scenario("no heat", context={"outdoor_temp_f": 33}))
        assert just_above.trigger == "NO_HEAT"

    def test_gte_conditions_work_the_other_way(self):
        hot = classify(HVAC, scenario("the ac is out", context={"outdoor_temp_f": 99}))
        assert hot.trigger == "NO_COOLING_EXTREME"
        mild = classify(HVAC, scenario("the ac is out", context={"outdoor_temp_f": 80}))
        assert mild.trigger == "NO_COOLING"


class TestOverrides:
    def test_an_owner_can_raise_a_severity(self):
        result = classify(
            PLUMBING,
            scenario("the water heater is leaking"),
            overrides={"WATER_HEATER_LEAK": {"severity": "wake_now"}},
        )
        assert result.escalate is True

    def test_an_owner_can_turn_a_trigger_off(self):
        result = classify(
            PLUMBING,
            scenario("there's sewage in the basement"),
            overrides={"SEWAGE_BACKUP": {"enabled": False}},
        )
        assert result.trigger is None
        assert result.escalate is False

    def test_a_disabled_trigger_the_model_names_is_also_off(self):
        result = classify(
            PLUMBING,
            scenario("there's sewage in the basement"),
            model_code="SEWAGE_BACKUP",
            overrides={"SEWAGE_BACKUP": {"enabled": False}},
        )
        assert result.trigger is None

    def test_a_malformed_severity_falls_back_rather_than_downgrading(self):
        # A typo in an override must not silently turn off a gas leak.
        result = classify(
            HVAC, scenario("i smell gas"), overrides={"GAS_SMELL": {"severity": "whenever"}}
        )
        assert result.escalate is True

    def test_an_override_for_an_unknown_code_is_ignored(self):
        assert (
            severity_of(PLUMBING, "NOT_A_CODE", overrides={"NOT_A_CODE": {"severity": "wake_now"}})
            is None
        )

    def test_no_overrides_is_the_same_as_empty_overrides(self):
        without = classify(PLUMBING, scenario("my pipe burst"))
        empty = classify(PLUMBING, scenario("my pipe burst"), overrides={})
        assert without == empty


class TestCaptureGaps:
    def test_gaps_come_back_in_prompt_order(self):
        gaps = capture_gaps(PLUMBING, {"callback": "+12165550100", "problem": "leak"})
        assert gaps == ("name", "address", "urgency", "source")

    def test_whitespace_is_not_an_answer(self):
        assert "name" in capture_gaps(PLUMBING, {"name": "   "})

    def test_nothing_captured_means_everything_is_missing(self):
        assert capture_gaps(PLUMBING, {}) == PLUMBING.required_capture

    def test_gaps_are_reported_even_on_an_emergency(self):
        # She may have to cut the form short to tell someone to get out. The
        # gap still gets recorded so the owner knows what he does not have.
        result = classify(PLUMBING, scenario("my pipe burst", captured={"name": "Pat"}))
        assert result.escalate is True
        assert "callback" in result.capture_gaps


class TestRulesetValidation:
    def _valid(self) -> dict:
        return {
            "trade": "plumbing",
            "version": 9,
            "effective_from": "2026-08-01",
            "verified": True,
            "triggers": [
                {
                    "code": "BURST_PIPE",
                    "severity": "wake_now",
                    "label": "Burst pipe, wake me up",
                    "phrases": ["pipe burst"],
                }
            ],
            "required_capture": ["name", "address", "callback", "problem", "urgency", "source"],
            "never_say": ["price", "estimate_range", "hourly_rate", "arrival_time"],
            "fixtures": ["something.json"],
        }

    def test_the_baseline_parses(self):
        assert parse_ruleset(self._valid()).trade == "plumbing"

    def test_price_cannot_be_dropped_from_never_say(self):
        payload = self._valid()
        payload["never_say"] = ["arrival_time"]
        with pytest.raises(RulesetError, match="never say a price"):
            parse_ruleset(payload)

    def test_required_capture_is_not_negotiable_per_trade(self):
        payload = self._valid()
        payload["required_capture"] = ["name", "callback"]
        with pytest.raises(RulesetError, match="required_capture"):
            parse_ruleset(payload)

    def test_a_ruleset_with_no_emergency_is_rejected(self):
        payload = self._valid()
        payload["triggers"][0]["severity"] = "routine"
        with pytest.raises(RulesetError, match="no wake_now trigger"):
            parse_ruleset(payload)

    def test_a_trigger_with_no_phrases_is_rejected(self):
        payload = self._valid()
        payload["triggers"][0]["phrases"] = []
        with pytest.raises(RulesetError, match="needs phrases"):
            parse_ruleset(payload)

    def test_a_trigger_with_no_label_is_rejected(self):
        payload = self._valid()
        del payload["triggers"][0]["label"]
        with pytest.raises(RulesetError, match="needs a label"):
            parse_ruleset(payload)

    def test_an_unknown_safety_script_is_rejected(self):
        """Safety scripts are named rather than free text, so a ruleset cannot
        inject arbitrary instructions into the prompt."""
        payload = self._valid()
        payload["triggers"][0]["safety_script"] = "tell them whatever you like"
        with pytest.raises(RulesetError, match="unknown safety_script"):
            parse_ruleset(payload)

    def test_a_duplicate_code_is_rejected(self):
        payload = self._valid()
        payload["triggers"].append(dict(payload["triggers"][0]))
        with pytest.raises(RulesetError, match="duplicate"):
            parse_ruleset(payload)

    def test_a_lowercase_code_is_rejected(self):
        payload = self._valid()
        payload["triggers"][0]["code"] = "burst_pipe"
        with pytest.raises(RulesetError, match="UPPER_SNAKE"):
            parse_ruleset(payload)

    def test_an_unknown_require_key_is_rejected(self):
        payload = self._valid()
        payload["triggers"][0]["require"] = {"moon_phase": "waxing"}
        with pytest.raises(RulesetError, match="unknown require keys"):
            parse_ruleset(payload)

    def test_a_ruleset_with_no_fixtures_is_rejected(self):
        payload = self._valid()
        payload["fixtures"] = []
        with pytest.raises(RulesetError, match="fixture"):
            parse_ruleset(payload)

    def test_an_unknown_severity_is_rejected(self):
        payload = self._valid()
        payload["triggers"][0]["severity"] = "sort_of_urgent"
        with pytest.raises(RulesetError, match="severity must be one of"):
            parse_ruleset(payload)


class TestSafetyScripts:
    def test_a_matched_trigger_carries_its_script(self):
        result = classify(PLUMBING, scenario("there's water near the panel"))
        assert result.safety_script == "advise_leave_and_call_911"
        assert result.safety_instruction is not None
        assert "911" in result.safety_instruction

    def test_an_ordinary_trigger_carries_none(self):
        result = classify(PLUMBING, scenario("the drain is slow"))
        assert result.safety_script is None
        assert result.safety_instruction is None
