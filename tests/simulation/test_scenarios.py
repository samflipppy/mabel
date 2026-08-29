"""Run every recorded call.

04-REPO.md schedules this nightly against staging. It also runs in CI, because
it needs no database, no model and no network — the scenario supplies what the
caller said and what the model decided, and everything downstream of that is
real code.

What that buys: a change to the verticals engine, the QA pass, a tool handler
or the post-call arithmetic that would alter how a real call is handled fails
here, on a named call, with a sentence explaining what that call is.
"""

from __future__ import annotations

import pytest

from tests.simulation.harness import (
    assert_she_said_nothing_forbidden,
    check,
    load_scenarios,
    rendered_prompt,
    run_scenario,
)

SCENARIOS = load_scenarios()
IDS = [scenario["scenario_id"] for scenario in SCENARIOS]


def test_there_are_scenarios_to_run():
    # 04-REPO.md asks for around thirty.
    assert len(SCENARIOS) >= 25, f"only {len(SCENARIOS)} scenarios"


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
async def test_scenario(scenario):
    result = await run_scenario(scenario)
    problems = check(scenario, result)

    assert not problems, f"\n{scenario['scenario_id']}: {scenario['note']}\n  - " + "\n  - ".join(
        problems
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
async def test_she_never_quotes_a_figure(scenario):
    """Applied to every call regardless of what it is testing.

    The one exception is the scenario that deliberately has her quote a price
    to prove QA catches it.
    """
    result = await run_scenario(scenario)
    problems = assert_she_said_nothing_forbidden(result.transcript)

    if "quoted_price" in scenario["expect"].get("qa_flags", []):
        assert problems, "this scenario is supposed to contain a quoted figure"
        return

    assert not problems, f"{scenario['scenario_id']}: " + "; ".join(problems)


class TestCoverage:
    """The scenario set has to cover the things that actually go wrong, not
    thirty variations of the happy path."""

    def test_the_emergency_path_is_covered(self):
        assert any(s["expect"].get("escalated") for s in SCENARIOS)

    def test_the_non_emergency_at_2am_is_covered(self):
        # Waking a contractor for a slow drain is why he cancels.
        assert any(
            s["scenario_id"] == "slow_drain_2am" and s["expect"]["escalated"] is False
            for s in SCENARIOS
        )

    def test_every_qa_flag_fires_in_at_least_one_scenario(self):
        covered = {flag for s in SCENARIOS for flag in s["expect"].get("qa_flags", [])}
        for flag in ("quoted_price", "missed_emergency", "over_escalated", "lost_caller_early"):
            assert flag in covered, f"no scenario produces {flag}"

    def test_several_trades_are_covered(self):
        trades = {s.get("trade", "plumbing") for s in SCENARIOS}
        assert len(trades) >= 5, f"only {sorted(trades)} covered"

    def test_the_awkward_callers_are_covered(self):
        ids = set(IDS)
        for awkward in (
            "asks_for_a_price_four_times",
            "will_not_give_an_address",
            "wrong_number_at_3am",
            "hangs_up_after_four_seconds",
            "nobody_on_call",
        ):
            assert awkward in ids, f"missing the {awkward} scenario"

    def test_every_scenario_explains_itself(self):
        """A failing scenario should tell whoever broke it what call it is."""
        for scenario in SCENARIOS:
            assert len(scenario["note"].split()) >= 6, (
                f"{scenario['scenario_id']} has no useful note"
            )


class TestThePromptStillRenders:
    """The simulation runs under a real rendered prompt, so a change that
    breaks prompt rendering fails here too rather than only at a call."""

    @pytest.mark.parametrize(
        "trade", ["plumbing", "hvac", "electrical", "roofing", "locksmith", "towing", "restoration"]
    )
    def test_it_renders_for_every_trade(self, trade):
        prompt = rendered_prompt(trade)
        assert "You are Mabel" in prompt
        assert "Never state a price" in prompt
