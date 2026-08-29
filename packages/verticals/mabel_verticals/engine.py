"""Deciding whether to wake somebody up.

03-VOICE.md is explicit that phrases are hints for the prompt, not a matcher:
the model classifies, and the ruleset says what each category means and what it
triggers. That is the design, and it is the right one — a homeowner saying
"there's water coming through the light fixture" should be caught, and no list
of phrases catches that.

So this engine does two separate things, and keeping them separate is the whole
point:

1. **Interpretation.** The model names a trigger code. The ruleset decides what
   that code costs: wake the owner now, hold it for the 7am recap, or leave it
   routine. That decision is deterministic and is not the model's to make. A
   model that returns `ACTIVE_FLOODING` does not get to also decide that
   flooding is a routine call.

2. **A backstop.** An independent phrase pass over what the caller actually
   said. It is not what drives the escalation during the call — it runs after,
   in QA, and when the two disagree the call gets flagged. Phrases missing
   something the model caught is fine and expected. The model missing something
   the phrases caught is a `missed_emergency` flag and a human looks at it.

Tenant overrides layer on top of both. An owner who says "no hot water, call me
in the morning" gets that, and an owner who wants every sewage call at 3am gets
that too.

Pure. No I/O, no model call, no clock read. Everything it needs arrives as an
argument, which is why every branch below is covered by a fixture.
"""

from __future__ import annotations

import re
from typing import Any

from mabel_verticals.models import (
    Classification,
    Notify,
    Ruleset,
    Severity,
    Trigger,
    Urgency,
)

# wake_now is the only severity that costs somebody their sleep.
_ESCALATES = {Severity.WAKE_NOW}

_URGENCY = {
    Severity.WAKE_NOW: Urgency.EMERGENCY,
    Severity.MORNING: Urgency.SOON,
    Severity.ROUTINE: Urgency.ROUTINE,
}


def severity_of(
    ruleset: Ruleset, code: str, *, overrides: dict[str, Any] | None = None
) -> Severity | None:
    """What does this trigger code mean for this tenant?

    `overrides` is `agent_configs.emergency_overrides`, keyed by trigger code:

        {"WATER_HEATER_LEAK": {"severity": "wake_now"},
         "SLOW_DRAIN": {"enabled": false}}

    An unknown code returns None rather than guessing. If the model invents a
    category we have never heard of, we do not get to decide it is an
    emergency — the call falls through to the ordinary lead path and the QA
    pass flags the unknown code.
    """
    trigger = ruleset.by_code(code)
    if trigger is None:
        return None

    override = (overrides or {}).get(code) or {}
    if override.get("enabled") is False:
        return None
    raw = override.get("severity")
    if raw is not None:
        try:
            return Severity(raw)
        except ValueError:
            # A malformed override must not silently downgrade an emergency.
            # Fall through to the library default, which is the safe direction.
            return trigger.severity
    return trigger.severity


def _haystack(scenario: dict[str, Any]) -> str:
    parts = [str(item) for item in (scenario.get("utterances") or [])]
    captured = scenario.get("captured") or {}
    for key in ("problem", "urgency"):
        value = captured.get(key)
        if value:
            parts.append(str(value))
    return " ".join(parts).casefold()


def _conditions_met(trigger: Trigger, context: dict[str, Any]) -> bool:
    """`require` gates a trigger on facts that are not in the transcript.

    "No heat" is an emergency at 10 degrees and a routine call in June, and the
    outdoor temperature is something we look up, not something the caller says.
    A required fact that is missing means the condition is not met — we do not
    assume the worst, because assuming the worst here means waking someone at
    3am on a guess.
    """
    require = trigger.require or {}

    if "outdoor_temp_f_lte" in require:
        temp = context.get("outdoor_temp_f")
        if not isinstance(temp, int | float) or isinstance(temp, bool):
            return False
        if temp > require["outdoor_temp_f_lte"]:
            return False

    if "outdoor_temp_f_gte" in require:
        temp = context.get("outdoor_temp_f")
        if not isinstance(temp, int | float) or isinstance(temp, bool):
            return False
        if temp < require["outdoor_temp_f_gte"]:
            return False

    if "vulnerable_occupant" in require:
        wanted = bool(require["vulnerable_occupant"])
        return bool(context.get("vulnerable_occupant")) is wanted

    return True


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Word-boundary matching, with a plural allowance on the last word.

    Bounded, so "sewage" does not fire inside "sewaged" and "no water" does not
    fire inside "snow water". Plural-tolerant, because a caller says "the
    gutters are overflowing" while the phrase list says "gutter" — requiring an
    exact word there means writing every noun twice and forgetting half of them.
    """
    return re.compile(r"\b" + re.escape(phrase.casefold()) + r"(?:s|es|'s)?\b")


def match_phrases(
    ruleset: Ruleset, scenario: dict[str, Any], *, overrides: dict[str, Any] | None = None
) -> Trigger | None:
    """The backstop pass. Returns the most severe trigger whose phrases appear.

    Most severe rather than first-listed: a caller who says "my drain is slow
    and there's sewage coming up" has a sewage problem, and the order the
    triggers happen to sit in the JSON must not decide that.
    """
    text = _haystack(scenario)
    context = scenario.get("context") or {}

    best: Trigger | None = None
    best_rank = -1
    ranking = {Severity.WAKE_NOW: 2, Severity.MORNING: 1, Severity.ROUTINE: 0}

    for trigger in ruleset.triggers:
        effective = severity_of(ruleset, trigger.code, overrides=overrides)
        if effective is None:
            continue
        if not _conditions_met(trigger, context):
            continue
        if not any(pattern.search(text) for pattern in map(_phrase_pattern, trigger.phrases)):
            continue
        rank = ranking[effective]
        if rank > best_rank:
            best, best_rank = trigger, rank

    return best


def capture_gaps(ruleset: Ruleset, captured: dict[str, Any]) -> tuple[str, ...]:
    """What Mabel still has to ask for, in the order the prompt asks for it."""
    missing = {
        field
        for field in ruleset.required_capture
        if not str((captured or {}).get(field) or "").strip()
    }
    return tuple(field for field in ruleset.required_capture if field in missing)


def classify(
    ruleset: Ruleset,
    scenario: dict[str, Any],
    *,
    model_code: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> Classification:
    """The decision.

    `model_code` is what the voice agent classified the call as, if anything.
    When it is absent — a fixture, a post-call re-run, a call where she never
    called `escalate_emergency` — the phrase backstop stands in.

    The two are reconciled by taking the *more severe* of the two. A model that
    misses a burst pipe our phrases caught still wakes the owner; a model that
    catches something no phrase covers still wakes the owner. Neither can talk
    the other down, because the failure we care about is the one where nobody
    gets called.
    """
    gaps = capture_gaps(ruleset, scenario.get("captured") or {})
    context = scenario.get("context") or {}
    ranking = {Severity.WAKE_NOW: 2, Severity.MORNING: 1, Severity.ROUTINE: 0}

    from_model: tuple[Trigger, Severity] | None = None
    if model_code:
        trigger = ruleset.by_code(model_code)
        severity = severity_of(ruleset, model_code, overrides=overrides)
        # A trigger the model named still has to satisfy its own conditions.
        # Otherwise "no heat" in June escalates because the model said so.
        if trigger is not None and severity is not None and _conditions_met(trigger, context):
            from_model = (trigger, severity)

    matched = match_phrases(ruleset, scenario, overrides=overrides)
    from_phrases: tuple[Trigger, Severity] | None = None
    if matched is not None:
        severity = severity_of(ruleset, matched.code, overrides=overrides)
        if severity is not None:
            from_phrases = (matched, severity)

    winner, matched_by = _reconcile(from_model, from_phrases, ranking)

    if winner is None:
        return Classification(
            trigger=None,
            severity=None,
            escalate=False,
            notify=Notify.RECAP_7AM,
            urgency=Urgency.ROUTINE,
            capture_gaps=gaps,
            matched_by=matched_by,
        )

    trigger, severity = winner
    escalate = severity in _ESCALATES
    return Classification(
        trigger=trigger.code,
        severity=severity,
        escalate=escalate,
        notify=Notify.NOW if escalate else Notify.RECAP_7AM,
        urgency=_URGENCY[severity],
        capture_gaps=gaps,
        safety_script=trigger.safety_script,
        matched_by=matched_by,
    )


def _reconcile(
    from_model: tuple[Trigger, Severity] | None,
    from_phrases: tuple[Trigger, Severity] | None,
    ranking: dict[Severity, int],
) -> tuple[tuple[Trigger, Severity] | None, str]:
    if from_model is None and from_phrases is None:
        return None, "none"
    if from_model is None:
        return from_phrases, "phrases"
    if from_phrases is None:
        return from_model, "model"
    if from_model[0].code == from_phrases[0].code:
        return from_model, "both"
    # They disagree. Take the more severe and record that they did, so the QA
    # pass has something to look at.
    if ranking[from_phrases[1]] > ranking[from_model[1]]:
        return from_phrases, "phrases_over_model"
    return from_model, "model_over_phrases"


def disagreed(classification: Classification) -> bool:
    """Did the model and the backstop reach different answers? Drives the
    `missed_emergency` QA flag."""
    return classification.matched_by in {"phrases_over_model", "model_over_phrases"}
