from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
RULES_ROOT = PACKAGE_ROOT
FIXTURES_DIR = PACKAGE_ROOT / "fixtures"

REQUIRED_CAPTURE = ("name", "address", "callback", "problem", "urgency", "source")
NEVER_SAY = ("price", "estimate_range", "hourly_rate", "arrival_time")
SEVERITIES = ("escalate_now",)


def rules_root() -> Path:
    return RULES_ROOT


def fixtures_dir() -> Path:
    return FIXTURES_DIR


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must be a JSON object")
    return payload


def validate_rules(rules: dict[str, Any], *, source: Path | None = None) -> None:
    label = str(source) if source else "rules"
    for key in (
        "vertical",
        "version",
        "effective_from",
        "verified",
        "emergency_triggers",
        "required_capture",
        "never_say",
        "fixtures",
    ):
        if key not in rules:
            raise ValueError(f"{label} is missing {key}")

    if not isinstance(rules["vertical"], str) or not rules["vertical"]:
        raise ValueError(f"{label} vertical must be a name")
    if not isinstance(rules["version"], int) or rules["version"] < 1:
        raise ValueError(f"{label} version must be a positive integer")
    if not isinstance(rules["verified"], bool):
        raise ValueError(f"{label} verified must be true or false")
    if not isinstance(rules["emergency_triggers"], list) or not rules["emergency_triggers"]:
        raise ValueError(f"{label} needs at least one emergency trigger")
    if list(rules["required_capture"]) != list(REQUIRED_CAPTURE):
        raise ValueError(
            f"{label} required_capture must be name/address/callback/problem/urgency/source"
        )
    if any(item not in NEVER_SAY for item in rules["never_say"]):
        raise ValueError(f"{label} never_say has an unknown item")
    if "price" not in rules["never_say"]:
        raise ValueError(f"{label} must never say a price")

    codes: set[str] = set()
    for trigger in rules["emergency_triggers"]:
        if not isinstance(trigger, dict):
            raise ValueError(f"{label} trigger must be an object")
        code = trigger.get("code")
        if not isinstance(code, str) or not code.isupper():
            raise ValueError(f"{label} trigger code must be UPPER_SNAKE")
        if code in codes:
            raise ValueError(f"{label} duplicate trigger {code}")
        codes.add(code)
        if trigger.get("severity") not in SEVERITIES:
            raise ValueError(f"{label} {code} severity must be escalate_now")
        phrases = trigger.get("phrases")
        if not isinstance(phrases, list) or not phrases:
            raise ValueError(f"{label} {code} needs phrases so matching stays deterministic")
        require = trigger.get("require") or {}
        if not isinstance(require, dict):
            raise ValueError(f"{label} {code} require must be an object")
        extra = set(require) - {"outdoor_temp_f_lte", "vulnerable_occupant"}
        if extra:
            raise ValueError(f"{label} {code} has unknown require keys: {sorted(extra)}")

    if not isinstance(rules["fixtures"], list) or not rules["fixtures"]:
        raise ValueError(f"{label} needs at least one fixture")


def load_rules(vertical: str, version: int) -> dict[str, Any]:
    path = RULES_ROOT / vertical / f"v{version}.json"
    if not path.is_file():
        raise FileNotFoundError(f"No rule file at {path}")
    rules = load_json(path)
    validate_rules(rules, source=path)
    if rules["vertical"] != vertical:
        raise ValueError(f"{path} vertical field does not match folder {vertical}")
    if rules["version"] != version:
        raise ValueError(f"{path} version field does not match filename")
    return rules


def load_latest_rules(vertical: str) -> dict[str, Any]:
    folder = RULES_ROOT / vertical
    versions = []
    for path in folder.glob("v*.json"):
        try:
            versions.append(int(path.stem[1:]))
        except ValueError:
            continue
    if not versions:
        raise FileNotFoundError(f"No rule files in {folder}")
    return load_rules(vertical, max(versions))


def load_fixture(name: str) -> dict[str, Any]:
    filename = name if name.endswith(".json") else f"{name}.json"
    path = FIXTURES_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"No fixture at {path}")
    fixture = load_json(path)
    _validate_fixture(fixture, source=path)
    return fixture


def iter_rule_files() -> list[Path]:
    return sorted(path for path in RULES_ROOT.glob("*/v*.json") if path.is_file())


def iter_fixtures() -> list[Path]:
    return sorted(FIXTURES_DIR.glob("*.json"))


def _validate_fixture(fixture: dict[str, Any], *, source: Path) -> None:
    for key in ("id", "vertical", "rule_version", "input", "expect"):
        if key not in fixture:
            raise ValueError(f"{source} is missing {key}")
    incoming = fixture["input"]
    expect = fixture["expect"]
    if "utterances" not in incoming or "captured" not in incoming:
        raise ValueError(f"{source} input needs utterances and captured")
    for key in ("trigger", "escalate", "notify", "capture_gaps"):
        if key not in expect:
            raise ValueError(f"{source} expect is missing {key}")
    if expect["notify"] not in {"now", "recap_7am"}:
        raise ValueError(f"{source} notify must be now or recap_7am")
    if expect["escalate"] and expect["notify"] != "now":
        raise ValueError(f"{source} an escalation must notify now")
    if not expect["escalate"] and expect["notify"] != "recap_7am":
        raise ValueError(f"{source} a non-emergency waits for the 7am recap")
