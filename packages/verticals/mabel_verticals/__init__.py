"""Deterministic vertical rule matching for Mabel.

An LLM never decides an emergency. Phrases in the rule JSON do.
"""

from __future__ import annotations

from mabel_verticals.evaluate import evaluate_scenario
from mabel_verticals.load import load_fixture, load_latest_rules, load_rules, rules_root

__all__ = [
    "evaluate_scenario",
    "load_fixture",
    "load_latest_rules",
    "load_rules",
    "rules_root",
]
