"""Golden fixtures for the vertical rule library.

A rule change without a fixture is a failed review.
"""

from mabel_verticals.harness import assert_every_rule_has_a_fixture, run_all_fixtures
from mabel_verticals.load import load_fixture, load_latest_rules, load_rules


def test_golden_fixtures_pass() -> None:
    assert_every_rule_has_a_fixture()
    failed = [item for item in run_all_fixtures() if not item["ok"]]
    assert failed == []


def test_burst_pipe_escalates_now() -> None:
    rules = load_rules("plumbing", 3)
    assert rules["verified"] is True
    fixture = load_fixture("plumbing_burst_pipe")
    assert fixture["expect"]["escalate"] is True
    assert fixture["expect"]["trigger"] == "BURST_PIPE"
    assert fixture["expect"]["notify"] == "now"


def test_slow_drain_at_2am_is_morning_recap() -> None:
    fixture = load_fixture("plumbing_slow_drain_2am")
    assert fixture["expect"]["escalate"] is False
    assert fixture["expect"]["notify"] == "recap_7am"


def test_draft_verticals_stay_unverified() -> None:
    for vertical in ("hvac", "electrical", "restoration"):
        rules = load_latest_rules(vertical)
        assert rules["verified"] is False
