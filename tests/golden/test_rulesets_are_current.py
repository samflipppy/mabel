"""The committed JSON must match what the build scripts produce.

The JSON files are the artifact the engine loads. The build scripts are how
they are authored. If someone edits a ruleset by hand and forgets the script —
or edits the script and forgets to run it — the two come apart silently, and
the next person to run the script reverts a rule change nobody remembers
making.

Fix a failure here by running:

    python packages/verticals/build_rulesets.py
    python packages/verticals/build_fixtures.py

and committing the result.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
VERTICALS = REPO / "packages" / "verticals"


def _load(name: str) -> ModuleType:
    path = VERTICALS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILD_RULESETS = _load("build_rulesets")
BUILD_FIXTURES = _load("build_fixtures")

ALL_FIXTURES = BUILD_FIXTURES.FIXTURES + BUILD_FIXTURES.OVERRIDE_FIXTURES


@pytest.mark.parametrize("ruleset", BUILD_RULESETS.ALL, ids=lambda r: r["trade"])
def test_ruleset_json_is_current(ruleset):
    path = VERTICALS / "rulesets" / f"{ruleset['trade']}.v{ruleset['version']}.json"
    assert path.is_file(), f"{path.name} has not been generated"
    assert json.loads(path.read_text(encoding="utf-8")) == ruleset, (
        f"{path.name} differs from build_rulesets.py. "
        "Run `python packages/verticals/build_rulesets.py` and commit."
    )


@pytest.mark.parametrize("item", ALL_FIXTURES, ids=lambda f: f["id"])
def test_fixture_json_is_current(item):
    path = VERTICALS / "fixtures" / f"{item['id']}.json"
    assert path.is_file(), f"{path.name} has not been generated"
    assert json.loads(path.read_text(encoding="utf-8")) == item, (
        f"{path.name} differs from build_fixtures.py. "
        "Run `python packages/verticals/build_fixtures.py` and commit."
    )


def test_no_stray_ruleset_files():
    expected = {f"{r['trade']}.v{r['version']}.json" for r in BUILD_RULESETS.ALL}
    on_disk = {p.name for p in (VERTICALS / "rulesets").glob("*.json")}
    assert on_disk == expected, (
        f"rulesets/ has files the build script does not produce: {sorted(on_disk - expected)}"
    )


def test_no_stray_fixture_files():
    expected = {f"{f['id']}.json" for f in ALL_FIXTURES}
    on_disk = {p.name for p in (VERTICALS / "fixtures").glob("*.json")}
    assert on_disk == expected, (
        f"fixtures/ has files the build script does not produce: {sorted(on_disk - expected)}"
    )


def test_fixture_ids_are_unique():
    ids = [f["id"] for f in ALL_FIXTURES]
    duplicates = {i for i in ids if ids.count(i) > 1}
    assert not duplicates, f"two fixtures share an id and one overwrote the other: {duplicates}"
