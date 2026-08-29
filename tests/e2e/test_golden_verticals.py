"""Golden vertical fixtures still pass. Every change to the rule library
needs a fixture; this file is the E2E reminder that the suite is still green.
"""

from __future__ import annotations

from mabel_verticals.engine import classify
from mabel_verticals.loader import TRADES, iter_fixture_paths, load_fixture, load_ruleset
from mabel_xai.client import FORBIDDEN_MODEL_ALIAS, VOICE_MODEL


def test_every_shipped_trade_still_has_a_ruleset():
    assert {"plumbing", "hvac", "electrical", "roofing"} <= set(TRADES)


def test_every_fixture_still_classifies_as_expected():
    paths = iter_fixture_paths()
    assert paths, "no golden fixtures found"
    for path in paths:
        fixture = load_fixture(path.name)
        ruleset = load_ruleset(fixture["trade"], fixture["rule_version"])
        result = classify(
            ruleset,
            fixture["input"],
            model_code=fixture.get("model_code"),
            overrides=fixture.get("overrides"),
        )
        expected = fixture["expect"]
        assert result.escalate is expected["escalate"], path.name
        assert result.notify.value == expected["notify"], path.name


def test_voice_stays_pinned_next_to_the_rules():
    assert VOICE_MODEL == "grok-voice-think-fast-2.0"
    assert FORBIDDEN_MODEL_ALIAS == "grok-voice-latest"
