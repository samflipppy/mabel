from __future__ import annotations

from mabel_verticals.evaluate import evaluate_scenario
from mabel_verticals.harness import assert_every_rule_has_a_fixture, run_all_fixtures, run_fixture
from mabel_verticals.load import (
    NEVER_SAY,
    iter_rule_files,
    load_fixture,
    load_json,
    load_latest_rules,
    load_rules,
    validate_rules,
)


def test_every_rule_file_loads_and_has_a_fixture() -> None:
    paths = iter_rule_files()
    assert paths, "expected vertical rule files"
    assert_every_rule_has_a_fixture()
    verticals = {load_json(path)["vertical"] for path in paths}
    assert verticals == {"plumbing", "hvac", "electrical", "restoration"}


def test_plumbing_v3_matches_the_product_spec() -> None:
    rules = load_rules("plumbing", 3)
    assert rules["verified"] is True
    assert rules["version"] == 3
    codes = [item["code"] for item in rules["emergency_triggers"]]
    assert codes == [
        "BURST_PIPE",
        "WATER_NEAR_ELECTRICAL",
        "SEWAGE_BACKUP",
        "NO_WATER_WHOLE_HOUSE",
        "ACTIVE_FLOODING",
    ]
    assert rules["required_capture"] == [
        "name",
        "address",
        "callback",
        "problem",
        "urgency",
        "source",
    ]
    assert rules["never_say"] == list(NEVER_SAY)
    assert "plumbing_burst_pipe.json" in rules["fixtures"]
    assert "plumbing_slow_drain_2am.json" in rules["fixtures"]


def test_draft_verticals_are_unverified() -> None:
    for vertical in ("hvac", "electrical", "restoration"):
        rules = load_latest_rules(vertical)
        assert rules["verified"] is False
        assert rules["fixtures"], f"{vertical} needs a fixture"


def test_all_golden_fixtures() -> None:
    results = run_all_fixtures()
    failed = [item for item in results if not item["ok"]]
    assert not failed, failed
    ids = {item["id"] for item in results}
    assert "plumbing_burst_pipe" in ids
    assert "plumbing_slow_drain_2am" in ids


def test_burst_pipe_escalates_now() -> None:
    result = run_fixture(load_fixture("plumbing_burst_pipe"))
    assert result["ok"]
    assert result["actual"]["trigger"] == "BURST_PIPE"
    assert result["actual"]["escalate"] is True
    assert result["actual"]["notify"] == "now"
    assert result["actual"]["capture_gaps"] == []


def test_slow_drain_at_2am_waits_for_morning() -> None:
    result = run_fixture(load_fixture("plumbing_slow_drain_2am"))
    assert result["ok"]
    assert result["actual"]["trigger"] is None
    assert result["actual"]["escalate"] is False
    assert result["actual"]["notify"] == "recap_7am"
    assert result["actual"]["capture_gaps"] == ["source"]


def test_no_heat_above_freezing_does_not_escalate() -> None:
    rules = load_latest_rules("hvac")
    actual = evaluate_scenario(
        rules,
        {
            "utterances": ["The furnace is out and there's no heat in the house."],
            "captured": {"problem": "no heat"},
            "context": {"outdoor_temp_f": 45},
        },
    )
    assert actual["trigger"] is None
    assert actual["escalate"] is False
    assert actual["notify"] == "recap_7am"


def test_ac_out_without_vulnerable_occupant_does_not_escalate() -> None:
    rules = load_latest_rules("hvac")
    actual = evaluate_scenario(
        rules,
        {
            "utterances": ["The AC is out and it's not cooling."],
            "captured": {"problem": "ac is out"},
            "context": {"vulnerable_occupant": False},
        },
    )
    assert actual["escalate"] is False
