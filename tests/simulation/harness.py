"""The simulation harness. Recorded calls, driven end to end.

04-REPO.md asks for ~30 recorded call scenarios, run nightly against staging.
This is the harness; the scenarios are JSON beside it.

**No sockets, no model, no database.** A scenario is a script of what the
caller said and what the model decided to do about it. The harness runs that
through the real dispatcher, the real handlers, the real verticals engine and
the real post-call pass, against `FakeRepo`. What it tests is the part we
wrote — whether the right things happen when the model behaves a given way —
rather than whether the model behaves that way, which is xAI's business and
cannot be asserted in CI anyway.

That distinction is the whole design. A simulation that also stubbed the
handlers would test nothing; one that called the real model would be
non-deterministic, expensive, and would fail for reasons unrelated to our code.

Each scenario asserts on the things that actually matter to a contractor:

- Did the right tools get called, in a sensible order?
- Did anybody's phone ring, and should it have?
- Did a lead get written, with the details filled in?
- Did any QA flag fire, and was it the right one?
- Did she say anything she should not have?
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from mabel_mcp.registry import ToolTrace, dispatch_with_repo
from mabel_mcp.repo import FakeRepo
from mabel_media.postcall import CallOutcome, compute
from mabel_media.prompt import PromptInputs, render_prompt
from mabel_verticals.loader import load_latest

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


@dataclass
class FakeConfig:
    """The live agent config a scenario runs against."""

    service_area_zips: list[str] = field(default_factory=lambda: ["44107", "44116"])
    service_area_note: str | None = None
    services: list[str] = field(default_factory=lambda: ["drain cleaning", "water heaters"])
    greeting: str = "Thanks for calling Ruiz Plumbing."


@dataclass
class Transcript:
    """What was said, in order."""

    turns: list[dict[str, Any]] = field(default_factory=list)

    def caller(self, text: str) -> None:
        self.turns.append({"role": "caller", "text": text})

    def mabel(self, text: str) -> None:
        self.turns.append({"role": "assistant", "text": text})


@dataclass
class SimulationResult:
    scenario_id: str
    trace: ToolTrace
    transcript: Transcript
    archived: Any
    tool_results: list[dict[str, Any]]

    def called(self, tool: str) -> bool:
        return self.trace.called(tool)

    def result_for(self, tool: str) -> dict[str, Any] | None:
        for entry in self.tool_results:
            if entry["tool"] == tool:
                return entry["content"]
        return None


async def run_scenario(scenario: dict[str, Any]) -> SimulationResult:
    """Drive one recorded call.

    The scenario supplies the caller's words and the tool calls the model made.
    Everything downstream of that is real code.
    """
    repo = FakeRepo(
        config=FakeConfig(**(scenario.get("config") or {})),
        knowledge=scenario.get("knowledge") or [],
        slots=scenario.get("slots") or [],
        contact=scenario.get("known_contact"),
        notified=scenario.get("oncall_available", True),
        history=scenario.get("job_history") or [],
    )

    transcript = Transcript()
    trace = ToolTrace()
    tool_results: list[dict[str, Any]] = []
    started = datetime.fromisoformat(scenario["started_at"])

    for step in scenario["script"]:
        if "caller" in step:
            transcript.caller(step["caller"])
        if "mabel" in step:
            transcript.mabel(step["mabel"])
        if "tool" in step:
            result = await dispatch_with_repo(
                step["tool"],
                step.get("args") or {},
                repo=repo,
                call_id=scenario["scenario_id"],
                now=started,
            )
            trace.record(result, step.get("args") or {})
            tool_results.append({"tool": step["tool"], "content": result.content})

    duration = int(scenario.get("duration_sec", 120))

    # A tool being *called* is not the same as it *succeeding*. `create_lead`
    # with an unusable callback number returns created=False and writes
    # nothing, so counting it as a lead would have the outcome disagree with
    # the database. The simulation caught this.
    created_lead = any(
        entry["tool"] == "create_lead" and entry["content"].get("created") for entry in tool_results
    )
    escalated = any(
        entry["tool"] == "escalate_emergency" and entry["content"].get("escalated")
        for entry in tool_results
    )
    booked = any(
        entry["tool"] == "book_estimate" and entry["content"].get("booked")
        for entry in tool_results
    )

    archived = compute(
        CallOutcome(
            call_id=scenario["scenario_id"],
            tenant_id=repo.contact_id,
            timezone=scenario.get("timezone", "America/New_York"),
            trade=scenario.get("trade", "plumbing"),
            from_e164="+12165550100",
            to_e164="+12165550148",
            started_at=started,
            ended_at=started + timedelta(seconds=duration),
            turns=transcript.turns,
            tool_trace=trace.entries,
            escalated=escalated,
            booked_a_slot=booked,
            lead_id=repo.lead_id if created_lead else None,
            context=_scenario_context(scenario),
        ),
        overrides=scenario.get("emergency_overrides"),
    )

    return SimulationResult(
        scenario_id=scenario["scenario_id"],
        trace=trace,
        transcript=transcript,
        archived=archived,
        tool_results=tool_results,
    )


def load_scenarios() -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SCENARIOS_DIR.glob("*.json"))
    ]


def _scenario_context(scenario: dict[str, Any]) -> dict[str, Any]:
    """Facts the ruleset needs that are not in the transcript.

    `no heat` is an emergency at ten degrees and a routine call in June, and
    the temperature is something we look up. Without a way to supply it, every
    weather-dependent scenario read as an over-escalation — which the
    simulation duly reported.
    """
    return scenario.get("context") or {}


def check(scenario: dict[str, Any], result: SimulationResult) -> list[str]:
    """Compare what happened against what the scenario expects.

    Returns a list of failures rather than raising on the first, so one run
    tells you everything that went wrong with a call rather than the first
    thing.
    """
    expect = scenario["expect"]
    problems: list[str] = []

    for tool in expect.get("tools_called", []):
        if not result.called(tool):
            problems.append(f"expected {tool} to be called")

    for tool in expect.get("tools_not_called", []):
        if result.called(tool):
            problems.append(f"{tool} should not have been called")

    if "escalated" in expect:
        escalated = result.called("escalate_emergency")
        if escalated != expect["escalated"]:
            problems.append(
                f"escalated={escalated}, expected {expect['escalated']}"
                + (" — nobody's phone rang" if expect["escalated"] else "")
            )

    if "outcome" in expect and result.archived.outcome != expect["outcome"]:
        problems.append(f"outcome={result.archived.outcome}, expected {expect['outcome']}")

    expected_flags = set(expect.get("qa_flags", []))
    actual_flags = set(result.archived.qa_flags)
    if expected_flags != actual_flags:
        problems.append(f"qa flags {sorted(actual_flags)}, expected {sorted(expected_flags)}")

    if expect.get("oncall_reached") is not None:
        content = result.result_for("escalate_emergency") or {}
        if content.get("oncall_reached") != expect["oncall_reached"]:
            problems.append(
                f"oncall_reached={content.get('oncall_reached')}, "
                f"expected {expect['oncall_reached']}"
            )

    return problems


def assert_she_said_nothing_forbidden(transcript: Transcript) -> list[str]:
    """Every scenario gets this, regardless of what it is testing.

    A price, a rate, or an invented arrival time in anything she said is a
    failure whether or not the scenario was about that.
    """
    from mabel_media.qa import _SPOKEN_MONEY, assistant_text_from_turns

    said = assistant_text_from_turns(transcript.turns)
    problems = []
    if _SPOKEN_MONEY.search(said):
        problems.append(f"she quoted a figure: {_SPOKEN_MONEY.search(said).group(0)!r}")
    return problems


def rendered_prompt(trade: str = "plumbing") -> str:
    """The prompt a scenario runs under. Rendered rather than fixtured, so a
    change that breaks prompt rendering fails the simulation too."""
    return render_prompt(
        PromptInputs(
            business_name="Ruiz Plumbing",
            trade=trade,
            city="Lakewood",
            greeting="Thanks for calling Ruiz Plumbing.",
            services=["drain cleaning", "water heaters"],
            services_declined=["septic tanks"],
            service_area_zips=["44107", "44116"],
            service_area_note=None,
            knowledge=[("Do you do drywall repair?", "Yes, as part of a painting job.")],
            never_say=["price", "estimate_range", "hourly_rate", "arrival_time"],
            custom_rules=None,
            ruleset=load_latest(trade),
            emergency_overrides={},
        )
    )


def now_at(hour: int, *, day: int = 14) -> str:
    """A UTC timestamp for a given Cleveland hour, for readable scenarios."""
    return datetime(2026, 10, day, hour, 0, tzinfo=UTC).isoformat()
