"""Trade rulesets and the engine that reads them.

Pure. No I/O beyond reading the bundled ruleset JSON, no model call, no clock.
The most-tested package in the repo, because it decides whether a phone rings
at two in the morning.
"""

from __future__ import annotations

from mabel_verticals.engine import (
    capture_gaps,
    classify,
    disagreed,
    match_phrases,
    severity_of,
)
from mabel_verticals.loader import (
    TRADES,
    available_versions,
    fixtures_dir,
    iter_fixture_paths,
    iter_ruleset_paths,
    load_fixture,
    load_latest,
    load_ruleset,
    parse_ruleset,
    ruleset_path,
    rulesets_dir,
    validate_fixture,
)
from mabel_verticals.models import (
    NEVER_SAY,
    REQUIRED_CAPTURE,
    SAFETY_SCRIPTS,
    Classification,
    Notify,
    Ruleset,
    RulesetError,
    Severity,
    Trigger,
    Urgency,
)

__all__ = [
    "NEVER_SAY",
    "REQUIRED_CAPTURE",
    "SAFETY_SCRIPTS",
    "TRADES",
    "Classification",
    "Notify",
    "Ruleset",
    "RulesetError",
    "Severity",
    "Trigger",
    "Urgency",
    "available_versions",
    "capture_gaps",
    "classify",
    "disagreed",
    "fixtures_dir",
    "iter_fixture_paths",
    "iter_ruleset_paths",
    "load_fixture",
    "load_latest",
    "load_ruleset",
    "match_phrases",
    "parse_ruleset",
    "ruleset_path",
    "rulesets_dir",
    "severity_of",
    "validate_fixture",
]
