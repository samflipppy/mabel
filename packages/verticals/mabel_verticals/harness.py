"""Run every golden fixture. Used by pytest and `python -m mabel_verticals`."""

from __future__ import annotations

import json
import sys
from typing import Any

from mabel_verticals.evaluate import evaluate_scenario
from mabel_verticals.load import (
    iter_fixtures,
    iter_rule_files,
    load_fixture,
    load_json,
    load_rules,
    validate_rules,
)


def run_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    rules = load_rules(fixture["vertical"], fixture["rule_version"])
    actual = evaluate_scenario(rules, fixture["input"])
    expected = fixture["expect"]
    mismatches: dict[str, Any] = {}
    for key in ("trigger", "escalate", "notify", "capture_gaps"):
        if actual[key] != expected[key]:
            mismatches[key] = {"expected": expected[key], "actual": actual[key]}
    return {
        "id": fixture["id"],
        "ok": not mismatches,
        "mismatches": mismatches,
        "actual": actual,
    }


def run_all_fixtures() -> list[dict[str, Any]]:
    results = []
    for path in iter_fixtures():
        fixture = load_fixture(path.name)
        results.append(run_fixture(fixture))
    return results


def assert_every_rule_has_a_fixture() -> None:
    for path in iter_rule_files():
        rules = load_json(path)
        validate_rules(rules, source=path)
        missing = [name for name in rules["fixtures"] if not (path.parent.parent / "fixtures" / name).is_file()]
        if missing:
            raise FileNotFoundError(f"{path} lists fixtures that are not on disk: {missing}")


def main() -> int:
    assert_every_rule_has_a_fixture()
    results = run_all_fixtures()
    failed = [item for item in results if not item["ok"]]
    print(json.dumps({"ran": len(results), "failed": len(failed), "results": results}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
