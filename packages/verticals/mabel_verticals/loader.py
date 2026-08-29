"""Loading and validating rulesets.

Reading bundled package data off disk is the one filesystem touch the pure
packages are allowed — the JSON ships inside the package, so it is closer to a
constant than to I/O. Nothing here opens a socket or a database.

Validation is strict and runs on load, not on use. A ruleset that would be
unsafe to run a call against should fail at import time in CI, not at 2am when
somebody's basement is filling up.
"""

from __future__ import annotations

import json
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

from mabel_verticals.models import (
    NEVER_SAY,
    REQUIRED_CAPTURE,
    SAFETY_SCRIPTS,
    Ruleset,
    RulesetError,
    Severity,
    Trigger,
)

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
RULESETS_DIR = PACKAGE_ROOT / "rulesets"
FIXTURES_DIR = PACKAGE_ROOT / "fixtures"

# The seven trades with a shipped ruleset. Adding one here without adding the
# JSON and its fixtures fails `test_every_trade_has_a_ruleset`.
TRADES: tuple[str, ...] = (
    "plumbing",
    "hvac",
    "electrical",
    "restoration",
    "roofing",
    "locksmith",
    "towing",
)


def rulesets_dir() -> Path:
    return RULESETS_DIR


def fixtures_dir() -> Path:
    return FIXTURES_DIR


def _read(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise RulesetError(f"{path.name} must be a JSON object")
    return payload


def parse_ruleset(payload: dict[str, Any], *, source: str = "ruleset") -> Ruleset:
    """Validate and build. Every failure here names the file and the reason,
    because the person hitting it is usually adding a trade at speed."""
    for key in (
        "trade",
        "version",
        "effective_from",
        "verified",
        "triggers",
        "required_capture",
        "never_say",
        "fixtures",
    ):
        if key not in payload:
            raise RulesetError(f"{source} is missing {key!r}")

    trade = payload["trade"]
    if not isinstance(trade, str) or not trade:
        raise RulesetError(f"{source} trade must be a non-empty name")

    version = payload["version"]
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise RulesetError(f"{source} version must be a positive integer")

    if not isinstance(payload["verified"], bool):
        raise RulesetError(f"{source} verified must be true or false")

    try:
        effective_from = date.fromisoformat(payload["effective_from"])
    except (TypeError, ValueError) as exc:
        raise RulesetError(f"{source} effective_from must be an ISO date") from exc

    if list(payload["required_capture"]) != list(REQUIRED_CAPTURE):
        raise RulesetError(
            f"{source} required_capture must be exactly {list(REQUIRED_CAPTURE)}. "
            "The prompt asks for these six in this order; a ruleset does not get "
            "to change what Mabel collects."
        )

    never_say = tuple(payload["never_say"])
    unknown = [item for item in never_say if item not in NEVER_SAY]
    if unknown:
        raise RulesetError(f"{source} never_say has entries we do not render: {unknown}")
    if "price" not in never_say:
        # Invariant 4. Not negotiable per trade.
        raise RulesetError(f"{source} must never say a price")

    triggers = _parse_triggers(payload["triggers"], source=source)

    fixtures = tuple(payload["fixtures"])
    if not fixtures:
        raise RulesetError(
            f"{source} ships no fixtures. Every change to packages/verticals/ "
            "ships with a fixture; a rule without a test is a guess."
        )

    return Ruleset(
        trade=trade,
        version=version,
        effective_from=effective_from,
        verified=bool(payload["verified"]),
        triggers=triggers,
        required_capture=tuple(payload["required_capture"]),
        never_say=never_say,
        fixtures=fixtures,
    )


def _parse_triggers(raw: Any, *, source: str) -> tuple[Trigger, ...]:
    if not isinstance(raw, list) or not raw:
        raise RulesetError(f"{source} needs at least one trigger")

    triggers: list[Trigger] = []
    seen: set[str] = set()

    for entry in raw:
        if not isinstance(entry, dict):
            raise RulesetError(f"{source} trigger must be an object")

        code = entry.get("code")
        if not isinstance(code, str) or not code or code != code.upper():
            raise RulesetError(f"{source} trigger code must be UPPER_SNAKE, got {code!r}")
        if code in seen:
            raise RulesetError(f"{source} has a duplicate trigger {code}")
        seen.add(code)

        try:
            severity = Severity(entry.get("severity"))
        except ValueError as exc:
            raise RulesetError(
                f"{source} {code} severity must be one of "
                f"{[s.value for s in Severity]}, got {entry.get('severity')!r}"
            ) from exc

        phrases = entry.get("phrases")
        if not isinstance(phrases, list) or not phrases:
            raise RulesetError(
                f"{source} {code} needs phrases. They are hints for the prompt and "
                "the input to the QA backstop; a trigger with none is invisible to both."
            )
        if any(not isinstance(p, str) or not p.strip() for p in phrases):
            raise RulesetError(f"{source} {code} has an empty phrase")

        label = entry.get("label")
        if not isinstance(label, str) or not label.strip():
            raise RulesetError(
                f"{source} {code} needs a label. The portal shows the owner a "
                "sentence, not a JSON key."
            )

        safety_script = entry.get("safety_script")
        if safety_script is not None and safety_script not in SAFETY_SCRIPTS:
            raise RulesetError(
                f"{source} {code} names an unknown safety_script {safety_script!r}. "
                f"Known: {sorted(SAFETY_SCRIPTS)}. Scripts are named rather than free "
                "text so a ruleset cannot inject arbitrary instructions into the prompt."
            )

        require = entry.get("require") or {}
        if not isinstance(require, dict):
            raise RulesetError(f"{source} {code} require must be an object")
        extra = set(require) - {
            "outdoor_temp_f_lte",
            "outdoor_temp_f_gte",
            "vulnerable_occupant",
        }
        if extra:
            raise RulesetError(f"{source} {code} has unknown require keys: {sorted(extra)}")

        triggers.append(
            Trigger(
                code=code,
                severity=severity,
                phrases=tuple(phrases),
                label=label,
                safety_script=safety_script,
                require=require,
            )
        )

    if not any(t.severity is Severity.WAKE_NOW for t in triggers):
        raise RulesetError(
            f"{source} has no wake_now trigger. Every trade has something that "
            "justifies a 2am phone call; a ruleset with none is almost certainly "
            "incomplete."
        )

    return tuple(triggers)


def ruleset_path(trade: str, version: int) -> Path:
    return RULESETS_DIR / f"{trade}.v{version}.json"


@cache
def load_ruleset(trade: str, version: int) -> Ruleset:
    path = ruleset_path(trade, version)
    if not path.is_file():
        raise FileNotFoundError(f"no ruleset at {path}")
    ruleset = parse_ruleset(_read(path), source=path.name)
    if ruleset.trade != trade:
        raise RulesetError(f"{path.name} declares trade {ruleset.trade!r}")
    if ruleset.version != version:
        raise RulesetError(f"{path.name} declares version {ruleset.version}")
    return ruleset


def available_versions(trade: str) -> tuple[int, ...]:
    versions = []
    for path in RULESETS_DIR.glob(f"{trade}.v*.json"):
        try:
            versions.append(int(path.stem.rsplit(".v", 1)[1]))
        except (IndexError, ValueError):
            continue
    return tuple(sorted(versions))


def load_latest(trade: str) -> Ruleset:
    versions = available_versions(trade)
    if not versions:
        raise FileNotFoundError(f"no rulesets for trade {trade!r} in {RULESETS_DIR}")
    return load_ruleset(trade, versions[-1])


def iter_ruleset_paths() -> list[Path]:
    return sorted(RULESETS_DIR.glob("*.v*.json"))


def iter_fixture_paths() -> list[Path]:
    return sorted(FIXTURES_DIR.glob("*.json"))


def load_fixture(name: str) -> dict[str, Any]:
    filename = name if name.endswith(".json") else f"{name}.json"
    path = FIXTURES_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"no fixture at {path}")
    fixture = _read(path)
    validate_fixture(fixture, source=filename)
    return fixture


def validate_fixture(fixture: dict[str, Any], *, source: str) -> None:
    for key in ("id", "trade", "rule_version", "input", "expect"):
        if key not in fixture:
            raise RulesetError(f"fixture {source} is missing {key!r}")

    incoming = fixture["input"]
    if "utterances" not in incoming or "captured" not in incoming:
        raise RulesetError(f"fixture {source} input needs utterances and captured")

    expect = fixture["expect"]
    for key in ("trigger", "escalate", "notify", "urgency", "capture_gaps"):
        if key not in expect:
            raise RulesetError(f"fixture {source} expect is missing {key!r}")

    # The two facts that must never come apart: an escalation notifies now, and
    # anything else waits for the recap. A fixture asserting otherwise is
    # describing a bug.
    if expect["escalate"] and expect["notify"] != "now":
        raise RulesetError(f"fixture {source}: an escalation must notify now")
    if not expect["escalate"] and expect["notify"] != "recap_7am":
        raise RulesetError(f"fixture {source}: a non-escalation waits for the 7am recap")
    if expect["escalate"] and expect["urgency"] != "emergency":
        raise RulesetError(f"fixture {source}: an escalation is urgency 'emergency'")
