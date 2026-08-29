"""Author the seven trade rulesets as JSON.

The JSON files are the artifact — this script exists so the seven of them stay
structurally identical to each other, and so a reviewer reads seven trades side
by side rather than seven files that drifted apart.

Run it after editing, then commit the JSON:

    python packages/verticals/build_rulesets.py

`tests/golden/test_rulesets_are_current.py` fails if the committed JSON does not
match what this script produces, so the two cannot come apart.

**Every trigger needs a fixture.** The `fixtures` list on each ruleset names
them and the golden suite checks they exist and pass.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parent / "rulesets"

CAPTURE = ["name", "address", "callback", "problem", "urgency", "source"]
NEVER_SAY = ["price", "estimate_range", "hourly_rate", "arrival_time"]


def trigger(
    code: str,
    severity: str,
    label: str,
    phrases: list[str],
    *,
    safety_script: str | None = None,
    require: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "code": code,
        "severity": severity,
        "label": label,
        "phrases": phrases,
    }
    if safety_script:
        entry["safety_script"] = safety_script
    if require:
        entry["require"] = require
    return entry


PLUMBING = {
    "trade": "plumbing",
    "version": 3,
    "effective_from": "2026-08-01",
    "verified": True,
    "triggers": [
        trigger(
            "ACTIVE_FLOODING",
            "wake_now",
            "Water actively coming in or flooding, wake me",
            [
                "water everywhere",
                "flooding",
                "flooded",
                "pouring",
                "water coming in",
                "gushing",
                "ceiling is pouring",
            ],
            safety_script="advise_shut_off_water",
        ),
        trigger(
            "BURST_PIPE",
            "wake_now",
            "Burst or broken pipe, wake me",
            [
                "pipe burst",
                "burst pipe",
                "pipe broke",
                "broken pipe",
                "pipe is broken",
                "pipe split",
                "pipe let go",
            ],
            safety_script="advise_shut_off_water",
        ),
        trigger(
            "SEWAGE_BACKUP",
            "wake_now",
            "Sewage backing up into the house, wake me",
            [
                "sewage",
                "raw sewage",
                "sewer backup",
                "backing up",
                "toilet backing up",
                "waste coming up",
            ],
        ),
        trigger(
            "NO_WATER",
            "wake_now",
            "No water to the whole house, wake me",
            [
                "no water at all",
                "no water anywhere",
                "whole house",
                "no water in the house",
                "main water is out",
            ],
        ),
        trigger(
            "WATER_NEAR_ELECTRICAL",
            "wake_now",
            "Water near the electrical panel, wake me and tell them to get out",
            [
                "near the panel",
                # The simulation caught this: a caller saying "near the
                # electrical panel" matched nothing, because "near the panel"
                # is not a substring of it.
                "near the electrical panel",
                "near the breaker panel",
                "water and sparks",
                "water near electrical",
                "water in the breaker",
                "water on the outlet",
            ],
            safety_script="advise_leave_and_call_911",
        ),
        trigger(
            "WATER_HEATER_LEAK",
            "morning",
            "Water heater leaking, the morning is fine",
            ["water heater leaking", "water heater is leaking", "hot water tank leaking"],
        ),
        trigger(
            "NO_HOT_WATER",
            "morning",
            "No hot water, the morning is fine",
            ["no hot water", "cold showers", "water heater is out"],
        ),
        trigger(
            "SLOW_DRAIN",
            "routine",
            "Slow or clogged drain, whenever you get to it",
            ["slow drain", "clogged", "draining slowly", "backed up sink"],
        ),
    ],
    "required_capture": CAPTURE,
    "never_say": NEVER_SAY,
    "fixtures": [
        "plumbing_burst_pipe.json",
        "plumbing_slow_drain_2am.json",
        "plumbing_sewage_backup.json",
        "plumbing_water_near_panel.json",
        "plumbing_water_near_electrical_panel.json",
        "plumbing_no_hot_water.json",
        "plumbing_active_flooding_model_missed.json",
        "plumbing_override_water_heater_wakes_me.json",
        "plumbing_override_sewage_muted.json",
        "plumbing_no_water_whole_house.json",
        "plumbing_water_heater_leak_default.json",
    ],
}

HVAC = {
    "trade": "hvac",
    "version": 2,
    "effective_from": "2026-08-01",
    "verified": True,
    "triggers": [
        trigger(
            "GAS_SMELL",
            "wake_now",
            "Smell of gas, wake me and tell them to get out",
            ["smell gas", "smell of gas", "gas leak", "smells like gas", "rotten eggs"],
            safety_script="advise_shut_off_gas_and_leave",
        ),
        trigger(
            "CO_ALARM",
            "wake_now",
            "Carbon monoxide alarm going off, wake me and tell them to get out",
            ["carbon monoxide", "co alarm", "co detector", "monoxide alarm"],
            safety_script="advise_leave_and_call_911",
        ),
        trigger(
            "NO_HEAT_FREEZING",
            "wake_now",
            "No heat when it is freezing outside, wake me",
            ["no heat", "furnace is out", "furnace stopped", "heat is out", "no heat at all"],
            require={"outdoor_temp_f_lte": 32},
        ),
        trigger(
            "NO_HEAT_VULNERABLE",
            "wake_now",
            "No heat with an infant, an elderly or an unwell person in the house, wake me",
            ["no heat", "furnace is out", "heat is out"],
            require={"vulnerable_occupant": True},
        ),
        trigger(
            "NO_COOLING_EXTREME",
            "wake_now",
            "No cooling in extreme heat, wake me",
            ["no ac", "no air conditioning", "ac is out", "air conditioning is out"],
            require={"outdoor_temp_f_gte": 95},
        ),
        trigger(
            "NO_HEAT",
            "morning",
            "No heat in mild weather, the morning is fine",
            ["no heat", "furnace is out", "heat is out", "furnace not working"],
        ),
        trigger(
            "NO_COOLING",
            "morning",
            "No cooling in ordinary weather, the morning is fine",
            ["no ac", "no air conditioning", "ac is out", "ac not working"],
        ),
        trigger(
            "THERMOSTAT_ISSUE",
            "routine",
            "Thermostat trouble, whenever you get to it",
            ["thermostat", "blank screen on the thermostat"],
        ),
    ],
    "required_capture": CAPTURE,
    "never_say": NEVER_SAY,
    "fixtures": [
        "hvac_no_heat_freezing.json",
        "hvac_no_heat_mild.json",
        "hvac_no_heat_vulnerable.json",
        "hvac_gas_smell.json",
        "hvac_co_alarm.json",
        "hvac_no_cooling_extreme.json",
        "hvac_thermostat_routine.json",
        "hvac_override_bad_severity_falls_back.json",
        "hvac_no_cooling_ordinary.json",
    ],
}

ELECTRICAL = {
    "trade": "electrical",
    "version": 2,
    "effective_from": "2026-08-01",
    "verified": True,
    "triggers": [
        trigger(
            "BURNING_SMELL",
            "wake_now",
            "Burning smell from an outlet or the panel, wake me and tell them to get out",
            [
                "burning smell",
                "smells like burning",
                "smell burning",
                "burning plastic",
                "something is burning",
            ],
            safety_script="advise_leave_and_call_911",
        ),
        trigger(
            "SPARKING",
            "wake_now",
            "Sparking outlet or panel, wake me",
            ["sparking", "sparks", "arcing", "outlet sparked", "panel is sparking"],
            safety_script="advise_leave_and_call_911",
        ),
        trigger(
            "DOWNED_LINE",
            "wake_now",
            "Downed power line, wake me and tell them to stay away",
            ["downed line", "power line down", "wire down", "line is down"],
            safety_script="advise_leave_and_call_911",
        ),
        trigger(
            "WATER_AND_ELECTRICAL",
            "wake_now",
            "Water reaching electrical, wake me and tell them to get out",
            ["water on the panel", "water in the panel", "water near the breaker"],
            safety_script="advise_leave_and_call_911",
        ),
        trigger(
            "FULL_OUTAGE",
            "wake_now",
            "Whole house has no power, wake me",
            ["no power at all", "whole house has no power", "everything is out"],
        ),
        trigger(
            "PARTIAL_OUTAGE",
            "morning",
            "Part of the house has no power, the morning is fine",
            ["half the house", "some outlets", "breaker keeps tripping", "one room has no power"],
        ),
        trigger(
            "OUTLET_NOT_WORKING",
            "routine",
            "A single outlet or switch out, whenever you get to it",
            ["outlet not working", "switch not working", "one outlet is dead"],
        ),
    ],
    "required_capture": CAPTURE,
    "never_say": NEVER_SAY,
    "fixtures": [
        "electrical_burning_smell.json",
        "electrical_sparking_panel.json",
        "electrical_partial_outage.json",
        "electrical_outlet_routine.json",
        "electrical_downed_line.json",
        "electrical_full_outage.json",
        "electrical_water_in_panel.json",
    ],
}

RESTORATION = {
    "trade": "restoration",
    "version": 1,
    "effective_from": "2026-08-01",
    "verified": True,
    "triggers": [
        trigger(
            "ACTIVE_WATER_INTRUSION",
            "wake_now",
            "Water still coming in, wake me",
            ["water is still coming in", "still flooding", "water everywhere", "actively leaking"],
        ),
        trigger(
            "SEWAGE_CONTAMINATION",
            "wake_now",
            "Sewage contamination, wake me",
            ["sewage", "raw sewage", "black water", "contaminated water"],
            safety_script="advise_do_not_enter",
        ),
        trigger(
            "POST_FIRE",
            "wake_now",
            "Fire damage, wake me",
            ["after a fire", "fire damage", "house fire", "smoke damage"],
            safety_script="advise_do_not_enter",
        ),
        trigger(
            "STRUCTURAL_RISK",
            "wake_now",
            "Ceiling or floor looks like it might come down, wake me",
            ["ceiling is sagging", "ceiling might collapse", "floor is buckling"],
            safety_script="advise_do_not_enter",
        ),
        trigger(
            "STANDING_WATER_CONTAINED",
            "morning",
            "Water is stopped but standing, the morning is fine",
            ["standing water", "water has stopped", "wet carpet", "soaked carpet"],
        ),
        trigger(
            "MOLD_DISCOVERED",
            "morning",
            "Mold found, the morning is fine",
            ["mold", "mildew", "black spots on the wall"],
        ),
        trigger(
            "ODOR_COMPLAINT",
            "routine",
            "A smell they want looked at, whenever you get to it",
            ["musty smell", "damp smell", "odor"],
        ),
    ],
    "required_capture": CAPTURE,
    "never_say": NEVER_SAY,
    "fixtures": [
        "restoration_active_water.json",
        "restoration_sewage.json",
        "restoration_standing_water.json",
        "restoration_mold_morning.json",
        "restoration_post_fire.json",
        "restoration_structural_risk.json",
        "restoration_odor_routine.json",
    ],
}

ROOFING = {
    "trade": "roofing",
    "version": 1,
    "effective_from": "2026-08-01",
    "verified": True,
    "triggers": [
        trigger(
            "ACTIVE_LEAK_INTERIOR",
            "wake_now",
            "Roof leaking into the house right now, wake me",
            [
                "leaking into the house",
                "water coming through the ceiling",
                "dripping through the ceiling",
                "roof is leaking",
            ],
        ),
        trigger(
            "STORM_DAMAGE_OPEN",
            "wake_now",
            "Roof opened up by a storm, wake me",
            ["roof came off", "hole in the roof", "roof is open", "blew the roof"],
        ),
        trigger(
            "TREE_ON_ROOF",
            "wake_now",
            "Tree down on the roof, wake me",
            ["tree on the roof", "tree fell on the house", "tree through the roof"],
            safety_script="advise_do_not_enter",
        ),
        trigger(
            "MISSING_SHINGLES",
            "morning",
            "Shingles missing after weather, the morning is fine",
            ["missing shingles", "shingles came off", "lost some shingles"],
        ),
        trigger(
            "LEAK_STAIN_NO_ACTIVE",
            "morning",
            "A stain on the ceiling with nothing actively dripping, the morning is fine",
            ["water stain", "stain on the ceiling", "brown spot on the ceiling"],
        ),
        trigger(
            "GUTTER_ISSUE",
            "routine",
            "Gutter trouble, whenever you get to it",
            ["gutter", "downspout", "gutters overflowing"],
        ),
    ],
    "required_capture": CAPTURE,
    "never_say": NEVER_SAY,
    "fixtures": [
        "roofing_active_leak.json",
        "roofing_tree_on_roof.json",
        "roofing_missing_shingles.json",
        "roofing_gutter_routine.json",
        "roofing_storm_damage_open.json",
        "roofing_stain_no_active_leak.json",
    ],
}

LOCKSMITH = {
    "trade": "locksmith",
    "version": 1,
    "effective_from": "2026-08-01",
    "verified": True,
    "triggers": [
        trigger(
            "CHILD_OR_PET_LOCKED_IN",
            "wake_now",
            "Child or animal locked in a car or a house, wake me and tell them to call 911",
            [
                "baby is locked in",
                # People speak in contractions. The simulation caught a call
                # saying "my baby's locked in the car" matching nothing at all.
                "baby's locked in",
                "child is locked in",
                "child's locked in",
                "kid locked in the car",
                "kid's locked in",
                "dog is locked in",
                "dog's locked in",
                "pet locked in the car",
            ],
            safety_script="advise_call_911_child_or_pet",
        ),
        trigger(
            "BURGLARY_DAMAGE",
            "wake_now",
            "Break-in, door or lock damaged, wake me",
            ["broke in", "break in", "burglary", "someone forced the door", "kicked in the door"],
            safety_script="advise_call_police_first",
        ),
        trigger(
            "LOCKED_OUT",
            "wake_now",
            "Locked out of a house or a car, wake me",
            ["locked out", "locked myself out", "keys are inside", "can't get in"],
        ),
        trigger(
            "BROKEN_KEY_IN_LOCK",
            "morning",
            "Key snapped off in the lock, the morning is fine",
            ["key broke", "broken key", "key snapped", "key stuck in the lock"],
        ),
        trigger(
            "SAFE_LOCKOUT",
            "morning",
            "Locked out of a safe, the morning is fine",
            ["safe won't open", "locked out of the safe", "forgot the safe combination"],
        ),
        trigger(
            "REKEY_REQUEST",
            "routine",
            "Rekey or new locks, whenever you get to it",
            ["rekey", "change the locks", "new locks", "duplicate key"],
        ),
    ],
    "required_capture": CAPTURE,
    "never_say": NEVER_SAY,
    "fixtures": [
        "locksmith_child_in_car.json",
        "locksmith_child_contraction.json",
        "locksmith_locked_out.json",
        "locksmith_broken_key.json",
        "locksmith_rekey_routine.json",
        "locksmith_burglary.json",
        "locksmith_safe_lockout.json",
        "locksmith_safe_lockout_worded_as_locked_out.json",
    ],
}

TOWING = {
    "trade": "towing",
    "version": 1,
    "effective_from": "2026-08-01",
    "verified": True,
    "triggers": [
        trigger(
            "ACCIDENT_SCENE",
            "wake_now",
            "Crash or collision, wake me",
            ["accident", "crashed", "collision", "wreck", "hit another car"],
            safety_script="advise_stay_with_vehicle",
        ),
        trigger(
            "BLOCKING_TRAFFIC",
            "wake_now",
            "Vehicle stopped in a traffic lane, wake me",
            [
                "blocking traffic",
                "in the middle of the road",
                "on the highway",
                "in a traffic lane",
                "on the freeway",
            ],
            safety_script="advise_stay_with_vehicle",
        ),
        trigger(
            "STRANDED_VULNERABLE",
            "wake_now",
            "Stranded with a child, an elderly or an unwell passenger, wake me",
            ["stranded", "broken down", "stuck on the side"],
            require={"vulnerable_occupant": True},
        ),
        trigger(
            "STRANDED_ROADSIDE",
            "wake_now",
            "Stranded at the roadside, wake me",
            ["stranded", "broken down", "stuck on the side of the road", "won't move"],
        ),
        trigger(
            "VEHICLE_WONT_START",
            "morning",
            "Car will not start at home, the morning is fine",
            ["won't start", "will not start", "dead battery", "car won't turn over"],
        ),
        trigger(
            "SCHEDULED_TRANSPORT",
            "routine",
            "A tow they want booked in advance, whenever you get to it",
            ["schedule a tow", "move a vehicle", "transport a car", "book a tow"],
        ),
    ],
    "required_capture": CAPTURE,
    "never_say": NEVER_SAY,
    "fixtures": [
        "towing_blocking_traffic.json",
        "towing_stranded_night.json",
        "towing_wont_start_morning.json",
        "towing_scheduled_routine.json",
        "towing_accident_scene.json",
        "towing_stranded_vulnerable.json",
    ],
}

ALL = [PLUMBING, HVAC, ELECTRICAL, RESTORATION, ROOFING, LOCKSMITH, TOWING]


def render(ruleset: dict[str, Any]) -> str:
    return json.dumps(ruleset, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for ruleset in ALL:
        path = OUT / f"{ruleset['trade']}.v{ruleset['version']}.json"
        path.write_text(render(ruleset), encoding="utf-8", newline="\n")
        print(f"wrote {path.name}: {len(ruleset['triggers'])} triggers")


if __name__ == "__main__":
    main()
