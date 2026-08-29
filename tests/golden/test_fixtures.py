"""Every fixture, against the engine.

A fixture is one call and one claim about what should happen to somebody's
sleep. If a fixture fails, either the rule changed and the fixture should have
changed with it, or the change was a mistake. Both are worth stopping for.

Every change to `packages/verticals/` ships with a fixture. `test_every_trigger_has_a_fixture`
is the thing that makes that a rule rather than a wish.
"""

from __future__ import annotations

import json

import pytest

from mabel_verticals.engine import classify
from mabel_verticals.loader import (
    TRADES,
    fixtures_dir,
    iter_fixture_paths,
    iter_ruleset_paths,
    load_fixture,
    load_latest,
    load_ruleset,
    parse_ruleset,
    ruleset_path,
)
from mabel_verticals.models import Severity

FIXTURE_PATHS = iter_fixture_paths()
FIXTURE_IDS = [p.stem for p in FIXTURE_PATHS]


def test_there_are_fixtures_to_run():
    assert FIXTURE_PATHS, f"no fixtures found in {fixtures_dir()}"


@pytest.mark.parametrize("path", FIXTURE_PATHS, ids=FIXTURE_IDS)
def test_fixture(path):
    fixture = load_fixture(path.name)
    ruleset = load_ruleset(fixture["trade"], fixture["rule_version"])

    result = classify(
        ruleset,
        fixture["input"],
        model_code=fixture.get("model_code"),
        overrides=fixture.get("overrides"),
    )
    expected = fixture["expect"]

    note = fixture.get("note", "")
    context = f"\n{path.name}: {note}" if note else f"\n{path.name}"

    assert result.trigger == expected["trigger"], f"wrong trigger{context}"
    assert (result.severity.value if result.severity else None) == expected["severity"], (
        f"wrong severity{context}"
    )
    assert result.escalate == expected["escalate"], f"wrong escalation{context}"
    assert result.notify.value == expected["notify"], f"wrong notify{context}"
    assert result.urgency.value == expected["urgency"], f"wrong urgency{context}"
    assert list(result.capture_gaps) == expected["capture_gaps"], f"wrong capture gaps{context}"

    if "matched_by" in expected:
        assert result.matched_by == expected["matched_by"], f"wrong matched_by{context}"


@pytest.mark.parametrize("trade", TRADES)
def test_every_trade_has_a_ruleset(trade: str):
    ruleset = load_latest(trade)
    assert ruleset.trade == trade
    assert ruleset.triggers


@pytest.mark.parametrize("trade", TRADES)
def test_every_trigger_has_a_fixture(trade: str):
    """The rule that has no exceptions. A trigger nobody wrote a fixture for is
    a trigger nobody has checked, and it is the one that fires at 3am."""
    ruleset = load_latest(trade)
    covered: set[str] = set()

    for name in ruleset.fixtures:
        fixture = load_fixture(name)
        expected = fixture["expect"]["trigger"]
        if expected:
            covered.add(expected)
        # An override fixture that mutes a trigger still counts as covering it:
        # it asserts what happens when the owner turns it off.
        for code in fixture.get("overrides") or {}:
            covered.add(code)

    uncovered = set(ruleset.codes) - covered
    assert not uncovered, (
        f"{trade} has triggers with no fixture: {sorted(uncovered)}. "
        "Every change to packages/verticals/ ships with a fixture. "
        "Add one in packages/verticals/build_fixtures.py."
    )


@pytest.mark.parametrize("path", iter_ruleset_paths(), ids=lambda p: p.stem)
def test_every_named_fixture_exists(path):
    ruleset = parse_ruleset(json.loads(path.read_text(encoding="utf-8")), source=path.name)
    missing = [name for name in ruleset.fixtures if not (fixtures_dir() / name).is_file()]
    assert not missing, f"{path.name} names fixtures that are not on disk: {missing}"


@pytest.mark.parametrize("path", FIXTURE_PATHS, ids=FIXTURE_IDS)
def test_every_fixture_belongs_to_a_ruleset(path):
    """No orphans. A fixture nobody's ruleset lists still runs above, but it
    would not be found by `test_every_trigger_has_a_fixture`, so coverage would
    quietly overstate itself."""
    fixture = json.loads(path.read_text(encoding="utf-8"))
    ruleset = load_ruleset(fixture["trade"], fixture["rule_version"])
    assert path.name in ruleset.fixtures, (
        f"{path.name} is not listed in {ruleset_path(ruleset.trade, ruleset.version).name}"
    )


class TestRulesetLibraryInvariants:
    """Properties that hold across every trade, checked so a new trade cannot
    quietly ship without them."""

    @pytest.mark.parametrize("trade", TRADES)
    def test_price_is_never_sayable(self, trade: str):
        assert "price" in load_latest(trade).never_say

    @pytest.mark.parametrize("trade", TRADES)
    def test_capture_is_the_same_six_fields_everywhere(self, trade: str):
        assert load_latest(trade).required_capture == (
            "name",
            "address",
            "callback",
            "problem",
            "urgency",
            "source",
        )

    @pytest.mark.parametrize("trade", TRADES)
    def test_every_trade_has_something_worth_waking_up_for(self, trade: str):
        wake = [t for t in load_latest(trade).triggers if t.severity is Severity.WAKE_NOW]
        assert wake, f"{trade} has no wake_now trigger"

    @pytest.mark.parametrize("trade", TRADES)
    def test_every_trade_has_something_not_worth_waking_up_for(self, trade: str):
        """A ruleset where everything is an emergency is a ruleset that gets
        the owner to turn his phone off, and then the real one gets missed."""
        quiet = [t for t in load_latest(trade).triggers if t.severity is not Severity.WAKE_NOW]
        assert quiet, f"{trade} treats everything as an emergency"

    @pytest.mark.parametrize("trade", TRADES)
    def test_every_trigger_reads_as_plain_english(self, trade: str):
        # The portal renders these as toggles the owner reads. A label that is
        # a restated JSON key helps nobody.
        for trigger in load_latest(trade).triggers:
            assert len(trigger.label.split()) >= 4, (
                f"{trade}.{trigger.code} label is too terse for the portal: {trigger.label!r}"
            )
            assert trigger.label[0].isupper(), (
                f"{trade}.{trigger.code} label should read as a sentence"
            )

    @pytest.mark.parametrize("trade", TRADES)
    def test_no_ruleset_mentions_money(self, trade: str):
        """Invariant 4, applied to the rule library itself. A phrase or label
        containing a dollar figure is a dollar figure one render away from the
        prompt."""
        payload = json.loads(
            ruleset_path(trade, load_latest(trade).version).read_text(encoding="utf-8")
        )
        # `never_say` is the list of things she must not say, so of course it
        # names them. What a caller or an owner actually reads is the triggers.
        readable = json.dumps(payload["triggers"]).lower()
        for token in ("$", "dollar", "price", "cost", "hourly", "estimate"):
            assert token not in readable, (
                f"{trade} ruleset mentions {token!r} in a trigger. A phrase or a "
                "label with money in it is money one render away from the prompt."
            )
