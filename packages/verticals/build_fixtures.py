"""Author the golden fixtures.

One fixture is one call, and one claim about what should happen to the owner's
sleep. They are generated from this file for the same reason the rulesets are:
thirty-odd near-identical JSON documents drift when they are edited by hand,
and a fixture that drifted is a test that stopped testing.

    python packages/verticals/build_fixtures.py

`tests/golden/` runs every one of them against the engine, and
`test_rulesets_are_current.py` fails if the committed JSON does not match what
this script produces.

**Every change to `packages/verticals/` ships with a fixture. No exceptions.**
Add the trigger here at the same time you add it to `build_rulesets.py`, or the
golden suite will tell you which trigger has no test.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parent / "fixtures"

FULL_CAPTURE = {
    "name": "Pat Example",
    "address": "100 Example Ave, Lakewood OH 44107",
    "callback": "+12165550100",
    "problem": "see utterances",
    "urgency": "as described",
    "source": "google",
}


def fixture(
    fixture_id: str,
    trade: str,
    rule_version: int,
    utterances: list[str],
    expect_trigger: str | None,
    expect_severity: str | None,
    *,
    called_at: str = "2026-01-15T23:12:00-05:00",
    captured: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    model_code: str | None = None,
    overrides: dict[str, Any] | None = None,
    expect_matched_by: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    escalate = expect_severity == "wake_now"
    urgency = {"wake_now": "emergency", "morning": "soon", "routine": "routine"}.get(
        expect_severity or "", "routine"
    )
    capture = {**FULL_CAPTURE, **(captured or {})}
    gaps = [k for k in FULL_CAPTURE if not str(capture.get(k) or "").strip()]

    payload: dict[str, Any] = {
        "id": fixture_id,
        "trade": trade,
        "rule_version": rule_version,
        "input": {
            "called_at": called_at,
            "utterances": utterances,
            "captured": capture,
            "context": context or {},
        },
        "expect": {
            "trigger": expect_trigger,
            "severity": expect_severity,
            "escalate": escalate,
            "notify": "now" if escalate else "recap_7am",
            "urgency": urgency,
            "capture_gaps": gaps,
        },
    }
    if model_code is not None:
        payload["model_code"] = model_code
    if overrides is not None:
        payload["overrides"] = overrides
    if expect_matched_by is not None:
        payload["expect"]["matched_by"] = expect_matched_by
    if note:
        payload["note"] = note
    return payload


FIXTURES: list[dict[str, Any]] = [
    # ---------------- plumbing ----------------
    fixture(
        "plumbing_burst_pipe",
        "plumbing",
        3,
        ["Hi, my pipe burst in the basement and I need someone out."],
        "BURST_PIPE",
        "wake_now",
        model_code="BURST_PIPE",
        expect_matched_by="both",
        note="The ordinary emergency. Model and backstop agree.",
    ),
    fixture(
        "plumbing_slow_drain_2am",
        "plumbing",
        3,
        ["My kitchen sink has a slow drain, it's been getting worse."],
        "SLOW_DRAIN",
        "routine",
        called_at="2026-01-16T02:04:00-05:00",
        captured={"source": ""},
        note=(
            "2am and still not an emergency. Escalating this is the "
            "over_escalated QA flag: waking a contractor at 2am for a slow "
            "drain is how he cancels."
        ),
    ),
    fixture(
        "plumbing_sewage_backup",
        "plumbing",
        3,
        ["There's raw sewage coming up through the basement floor drain."],
        "SEWAGE_BACKUP",
        "wake_now",
        model_code="SEWAGE_BACKUP",
        expect_matched_by="both",
    ),
    fixture(
        "plumbing_water_near_panel",
        "plumbing",
        3,
        ["Water is running down the wall near the panel in the basement."],
        "WATER_NEAR_ELECTRICAL",
        "wake_now",
        model_code="WATER_NEAR_ELECTRICAL",
        expect_matched_by="both",
        note="Carries a safety script. She tells them to leave before she finishes the form.",
    ),
    fixture(
        "plumbing_no_hot_water",
        "plumbing",
        3,
        ["We have no hot water this morning, everything else is fine."],
        "NO_HOT_WATER",
        "morning",
        note="Morning severity: a real job, not a reason to wake anybody.",
    ),
    fixture(
        "plumbing_active_flooding_model_missed",
        "plumbing",
        3,
        ["There's water everywhere, it's coming through the light fixture."],
        "ACTIVE_FLOODING",
        "wake_now",
        model_code="SLOW_DRAIN",
        expect_matched_by="phrases_over_model",
        note=(
            "The model called this a slow drain. The backstop caught it. The "
            "more severe answer wins and the call gets flagged, because the "
            "failure that matters is the one where nobody rings."
        ),
    ),
    # ---------------- hvac ----------------
    fixture(
        "hvac_no_heat_freezing",
        "hvac",
        2,
        ["We have no heat and it's ten degrees out."],
        "NO_HEAT_FREEZING",
        "wake_now",
        context={"outdoor_temp_f": 10},
        model_code="NO_HEAT_FREEZING",
        expect_matched_by="both",
    ),
    fixture(
        "hvac_no_heat_mild",
        "hvac",
        2,
        ["The furnace is out. It's not cold out, but we'd like it looked at."],
        "NO_HEAT",
        "morning",
        context={"outdoor_temp_f": 55},
        note=(
            "Same words as the 3am call, different weather, different answer. "
            "This is why `require` exists."
        ),
    ),
    fixture(
        "hvac_no_heat_vulnerable",
        "hvac",
        2,
        ["The heat is out and my mother is on oxygen here."],
        "NO_HEAT_VULNERABLE",
        "wake_now",
        context={"outdoor_temp_f": 55, "vulnerable_occupant": True},
        note="Mild outside, still an emergency, because of who is in the house.",
    ),
    fixture(
        "hvac_gas_smell",
        "hvac",
        2,
        ["I smell gas near the furnace."],
        "GAS_SMELL",
        "wake_now",
        model_code="GAS_SMELL",
        expect_matched_by="both",
    ),
    fixture(
        "hvac_co_alarm",
        "hvac",
        2,
        ["The carbon monoxide alarm keeps going off."],
        "CO_ALARM",
        "wake_now",
        model_code="CO_ALARM",
        expect_matched_by="both",
    ),
    fixture(
        "hvac_no_cooling_extreme",
        "hvac",
        2,
        ["The AC is out and it's ninety-nine degrees."],
        "NO_COOLING_EXTREME",
        "wake_now",
        context={"outdoor_temp_f": 99},
    ),
    fixture(
        "hvac_thermostat_routine",
        "hvac",
        2,
        ["The thermostat screen went blank, everything still runs."],
        "THERMOSTAT_ISSUE",
        "routine",
    ),
    # ---------------- electrical ----------------
    fixture(
        "electrical_burning_smell",
        "electrical",
        2,
        ["There's a burning smell coming from the outlet in the hallway."],
        "BURNING_SMELL",
        "wake_now",
        model_code="BURNING_SMELL",
        expect_matched_by="both",
    ),
    fixture(
        "electrical_sparking_panel",
        "electrical",
        2,
        ["The panel is sparking when I flip the main."],
        "SPARKING",
        "wake_now",
    ),
    fixture(
        "electrical_downed_line",
        "electrical",
        2,
        ["There's a power line down across my driveway."],
        "DOWNED_LINE",
        "wake_now",
    ),
    fixture(
        "electrical_partial_outage",
        "electrical",
        2,
        ["The breaker keeps tripping on the upstairs circuit."],
        "PARTIAL_OUTAGE",
        "morning",
    ),
    fixture(
        "electrical_outlet_routine",
        "electrical",
        2,
        ["One outlet is dead in the spare room, no rush."],
        "OUTLET_NOT_WORKING",
        "routine",
    ),
    # ---------------- restoration ----------------
    fixture(
        "restoration_active_water",
        "restoration",
        1,
        ["The water is still coming in from the upstairs bathroom."],
        "ACTIVE_WATER_INTRUSION",
        "wake_now",
        model_code="ACTIVE_WATER_INTRUSION",
        expect_matched_by="both",
    ),
    fixture(
        "restoration_sewage",
        "restoration",
        1,
        ["We've got raw sewage through the finished basement."],
        "SEWAGE_CONTAMINATION",
        "wake_now",
    ),
    fixture(
        "restoration_standing_water",
        "restoration",
        1,
        ["The leak's stopped but there's standing water on the floor."],
        "STANDING_WATER_CONTAINED",
        "morning",
    ),
    fixture(
        "restoration_mold_morning",
        "restoration",
        1,
        ["We found mold behind the washing machine."],
        "MOLD_DISCOVERED",
        "morning",
    ),
    # ---------------- roofing ----------------
    fixture(
        "roofing_active_leak",
        "roofing",
        1,
        ["The roof is leaking, there's water coming through the ceiling in the bedroom."],
        "ACTIVE_LEAK_INTERIOR",
        "wake_now",
        model_code="ACTIVE_LEAK_INTERIOR",
        expect_matched_by="both",
    ),
    fixture(
        "roofing_tree_on_roof",
        "roofing",
        1,
        ["A tree fell on the house during the storm."],
        "TREE_ON_ROOF",
        "wake_now",
    ),
    fixture(
        "roofing_missing_shingles",
        "roofing",
        1,
        ["We lost some shingles in the wind last night."],
        "MISSING_SHINGLES",
        "morning",
    ),
    fixture(
        "roofing_gutter_routine",
        "roofing",
        1,
        ["The gutters are overflowing at the back corner."],
        "GUTTER_ISSUE",
        "routine",
    ),
    # ---------------- locksmith ----------------
    fixture(
        "locksmith_child_in_car",
        "locksmith",
        1,
        ["My baby is locked in the car and it's running hot."],
        "CHILD_OR_PET_LOCKED_IN",
        "wake_now",
        model_code="CHILD_OR_PET_LOCKED_IN",
        expect_matched_by="both",
        note="911 before locksmith. The safety script says so and she says it first.",
    ),
    fixture(
        "locksmith_locked_out",
        "locksmith",
        1,
        ["I'm locked out of my house, my keys are inside."],
        "LOCKED_OUT",
        "wake_now",
    ),
    fixture(
        "locksmith_broken_key",
        "locksmith",
        1,
        ["The key snapped off in the back door lock, we can still use the front."],
        "BROKEN_KEY_IN_LOCK",
        "morning",
    ),
    fixture(
        "locksmith_rekey_routine",
        "locksmith",
        1,
        ["We just bought the place and want to change the locks."],
        "REKEY_REQUEST",
        "routine",
    ),
    # ---------------- towing ----------------
    fixture(
        "towing_blocking_traffic",
        "towing",
        1,
        ["I'm on the highway and the car died, I'm blocking traffic."],
        "BLOCKING_TRAFFIC",
        "wake_now",
        model_code="BLOCKING_TRAFFIC",
        expect_matched_by="both",
    ),
    fixture(
        "towing_stranded_night",
        "towing",
        1,
        ["I'm stranded on the side of the road out past the county line."],
        "STRANDED_ROADSIDE",
        "wake_now",
    ),
    fixture(
        "towing_wont_start_morning",
        "towing",
        1,
        ["The car won't start in my driveway, dead battery I think."],
        "VEHICLE_WONT_START",
        "morning",
    ),
    fixture(
        "towing_scheduled_routine",
        "towing",
        1,
        ["I need to schedule a tow for a project car next week."],
        "SCHEDULED_TRANSPORT",
        "routine",
    ),
]

# Triggers that the coverage test found had no fixture. Every one of these is a
# real call somebody makes.
FIXTURES += [
    fixture(
        "plumbing_no_water_whole_house",
        "plumbing",
        3,
        ["We've got no water at all, nothing in the whole house."],
        "NO_WATER",
        "wake_now",
    ),
    fixture(
        "plumbing_water_heater_leak_default",
        "plumbing",
        3,
        ["The water heater is leaking a bit onto the floor."],
        "WATER_HEATER_LEAK",
        "morning",
        note="The library default, so the override fixture has something to differ from.",
    ),
    fixture(
        "hvac_no_cooling_ordinary",
        "hvac",
        2,
        ["The AC is out upstairs, it's warm but bearable."],
        "NO_COOLING",
        "morning",
        context={"outdoor_temp_f": 78},
    ),
    fixture(
        "electrical_full_outage",
        "electrical",
        2,
        ["We have no power at all and the neighbours still have theirs."],
        "FULL_OUTAGE",
        "wake_now",
        note="Neighbours have power, so it is the house, not the utility. Ours to answer.",
    ),
    fixture(
        "electrical_water_in_panel",
        "electrical",
        2,
        ["There's water in the panel in the basement."],
        "WATER_AND_ELECTRICAL",
        "wake_now",
    ),
    fixture(
        "restoration_post_fire",
        "restoration",
        1,
        ["We had a house fire last night and need someone to look at it."],
        "POST_FIRE",
        "wake_now",
    ),
    fixture(
        "restoration_structural_risk",
        "restoration",
        1,
        ["The ceiling is sagging where the water came through."],
        "STRUCTURAL_RISK",
        "wake_now",
    ),
    fixture(
        "restoration_odor_routine",
        "restoration",
        1,
        ["There's a musty smell in the crawlspace we'd like checked."],
        "ODOR_COMPLAINT",
        "routine",
    ),
    fixture(
        "roofing_storm_damage_open",
        "roofing",
        1,
        ["The storm blew the roof off the back porch, there's a hole in the roof."],
        "STORM_DAMAGE_OPEN",
        "wake_now",
    ),
    fixture(
        "roofing_stain_no_active_leak",
        "roofing",
        1,
        ["There's a brown spot on the ceiling but nothing is dripping."],
        "LEAK_STAIN_NO_ACTIVE",
        "morning",
        note="A stain is a job. It is not a reason to wake anybody, and saying so is the point.",
    ),
    fixture(
        "locksmith_burglary",
        "locksmith",
        1,
        ["Someone kicked in the door while we were away."],
        "BURGLARY_DAMAGE",
        "wake_now",
    ),
    fixture(
        "locksmith_safe_lockout",
        "locksmith",
        1,
        ["The safe won't open at the shop, no rush tonight."],
        "SAFE_LOCKOUT",
        "morning",
    ),
    fixture(
        "locksmith_safe_lockout_worded_as_locked_out",
        "locksmith",
        1,
        ["We're locked out of the safe at the shop, no rush tonight."],
        "LOCKED_OUT",
        "wake_now",
        note=(
            "A known sharp edge, pinned here rather than left to be discovered. "
            "'Locked out of the safe' contains 'locked out', and the backstop "
            "takes the most severe match, so a safe lockout reads as a house "
            "lockout and wakes somebody. The model is the primary path and "
            "would say SAFE_LOCKOUT; the backstop is deliberately biased "
            "towards escalating. Over-calling here costs a phone call. "
            "Under-calling a real lockout at 1am costs the customer."
        ),
    ),
    fixture(
        "towing_accident_scene",
        "towing",
        1,
        ["I've been in an accident on Detroit Road, the car isn't drivable."],
        "ACCIDENT_SCENE",
        "wake_now",
    ),
    fixture(
        "towing_stranded_vulnerable",
        "towing",
        1,
        ["I'm broken down with my two kids in the back."],
        "STRANDED_VULNERABLE",
        "wake_now",
        context={"vulnerable_occupant": True},
    ),
]


# Phrasings the simulation harness found were missed. Every ruleset change
# ships with a fixture; these are those fixtures.
FIXTURES += [
    fixture(
        "plumbing_water_near_electrical_panel",
        "plumbing",
        3,
        ["Water is running down the wall near the electrical panel."],
        "WATER_NEAR_ELECTRICAL",
        "wake_now",
        note=(
            "The natural phrasing. 'near the panel' is not a substring of "
            "'near the electrical panel', so this matched nothing until the "
            "simulation ran it."
        ),
    ),
    fixture(
        "locksmith_child_contraction",
        "locksmith",
        1,
        ["My baby's locked in the car and it's hot."],
        "CHILD_OR_PET_LOCKED_IN",
        "wake_now",
        note=(
            "People speak in contractions. The phrase list had 'baby is locked "
            "in' and nothing else, so a real caller matched nothing."
        ),
    ),
]


# Fixtures that exercise the tenant override layer rather than a trigger.
OVERRIDE_FIXTURES: list[dict[str, Any]] = [
    fixture(
        "plumbing_override_water_heater_wakes_me",
        "plumbing",
        3,
        ["The water heater is leaking all over the utility room floor."],
        "WATER_HEATER_LEAK",
        "wake_now",
        overrides={"WATER_HEATER_LEAK": {"severity": "wake_now"}},
        note=(
            "An owner who wants water heaters at 3am gets water heaters at 3am. "
            "The library default is `morning`; his override raises it."
        ),
    ),
    fixture(
        "plumbing_override_sewage_muted",
        "plumbing",
        3,
        ["There's sewage backing up in the basement."],
        None,
        None,
        overrides={"SEWAGE_BACKUP": {"enabled": False}},
        note=(
            "And an owner who turns a trigger off gets it off. It becomes an "
            "ordinary lead in the 7am recap rather than a phone call. This is "
            "his business, not ours."
        ),
    ),
    fixture(
        "hvac_override_bad_severity_falls_back",
        "hvac",
        2,
        ["I smell gas by the furnace."],
        "GAS_SMELL",
        "wake_now",
        overrides={"GAS_SMELL": {"severity": "whenever"}},
        note=(
            "A malformed override does not silently downgrade a gas leak. It "
            "falls back to the library default, which is the safe direction to "
            "fail in."
        ),
    ),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    written = 0
    for item in FIXTURES + OVERRIDE_FIXTURES:
        path = OUT / f"{item['id']}.json"
        path.write_text(
            json.dumps(item, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
        written += 1
    print(f"wrote {written} fixtures to {OUT}")


if __name__ == "__main__":
    main()
