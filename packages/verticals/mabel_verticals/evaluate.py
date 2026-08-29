"""Match a call scenario against a vertical rule set.

Deterministic. No model. No dollar figures.
"""

from __future__ import annotations

from typing import Any

from mabel_verticals.load import REQUIRED_CAPTURE


def _haystack(scenario: dict[str, Any]) -> str:
    utterances = scenario.get("utterances") or []
    captured = scenario.get("captured") or {}
    parts = [str(item) for item in utterances]
    problem = captured.get("problem")
    if problem:
        parts.append(str(problem))
    return " ".join(parts).casefold()


def _conditions_met(trigger: dict[str, Any], context: dict[str, Any]) -> bool:
    require = trigger.get("require") or {}
    if "outdoor_temp_f_lte" in require:
        temp = context.get("outdoor_temp_f")
        if not isinstance(temp, int) or temp > require["outdoor_temp_f_lte"]:
            return False
    if "vulnerable_occupant" in require:
        if bool(context.get("vulnerable_occupant")) != bool(require["vulnerable_occupant"]):
            return False
    return True


def match_trigger(rules: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any] | None:
    text = _haystack(scenario)
    context = scenario.get("context") or {}
    for trigger in rules["emergency_triggers"]:
        if not _conditions_met(trigger, context):
            continue
        for phrase in trigger["phrases"]:
            if phrase.casefold() in text:
                return trigger
    return None


def capture_gaps(rules: dict[str, Any], captured: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    for field in rules["required_capture"]:
        value = captured.get(field)
        if value is None or str(value).strip() == "":
            gaps.append(field)
    # Keep the product order, never invent extra fields.
    return [field for field in REQUIRED_CAPTURE if field in gaps]


def evaluate_scenario(rules: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    trigger = match_trigger(rules, scenario)
    escalate = trigger is not None and trigger["severity"] == "escalate_now"
    return {
        "trigger": None if trigger is None else trigger["code"],
        "escalate": escalate,
        "notify": "now" if escalate else "recap_7am",
        "capture_gaps": capture_gaps(rules, scenario.get("captured") or {}),
    }
