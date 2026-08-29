"""The shapes a ruleset is made of.

Plain dataclasses rather than Pydantic, because `packages/verticals/` stays
independent of `packages/domain/` — the two pure packages do not import each
other. A ruleset describes an emergency; it does not need to know what a lead
is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    """What a matched trigger costs the owner.

    The portal renders these as plain English:
      wake_now  -> "Burst pipe or active flooding -> wake me"
      morning   -> "No hot water -> next morning is fine"
      routine   -> "Slow drain -> whenever"
    """

    WAKE_NOW = "wake_now"
    MORNING = "morning"
    ROUTINE = "routine"


class Notify(StrEnum):
    NOW = "now"
    RECAP_7AM = "recap_7am"


class Urgency(StrEnum):
    """Mirrors `leads.urgency` without importing it. The mapping from severity
    lives in the engine, deterministically."""

    EMERGENCY = "emergency"
    SOON = "soon"
    ROUTINE = "routine"


# Safety scripts are named, not free text, so the prompt renderer can only emit
# a line we wrote. A ruleset that could inject arbitrary instructions into the
# prompt is a ruleset that can be used to make her say anything.
SAFETY_SCRIPTS: dict[str, str] = {
    "advise_leave_and_call_911": (
        "Tell the caller to leave the building and call 911 now. "
        "Do not keep them on the line to finish collecting details."
    ),
    "advise_shut_off_water": (
        "Tell the caller where the main water shutoff usually is and ask them to "
        "close it if they can do so safely."
    ),
    "advise_shut_off_gas_and_leave": (
        "Tell the caller to leave the building immediately and call the gas "
        "utility from outside. Do not advise them to touch anything electrical."
    ),
    "advise_do_not_enter": (
        "Tell the caller not to re-enter the building until someone has been out."
    ),
    "advise_call_911_child_or_pet": (
        "Tell the caller to call 911 now. A child or an animal shut in a vehicle "
        "is a 911 call before it is a locksmith call, and saying so is not "
        "overstepping."
    ),
    "advise_call_police_first": (
        "Ask the caller to call the police before anyone comes out, and not to "
        "disturb the door or the damage until they have."
    ),
    "advise_stay_with_vehicle": (
        "If they are somewhere safe, ask them to stay with the vehicle. If they "
        "are in a traffic lane, tell them to get behind a barrier and call 911."
    ),
}

REQUIRED_CAPTURE: tuple[str, ...] = (
    "name",
    "address",
    "callback",
    "problem",
    "urgency",
    "source",
)

NEVER_SAY: tuple[str, ...] = ("price", "estimate_range", "hourly_rate", "arrival_time")


@dataclass(frozen=True, slots=True)
class Trigger:
    code: str
    severity: Severity
    phrases: tuple[str, ...]
    # Plain English for the portal's Emergencies tab. The owner toggles this
    # sentence, not a JSON key.
    label: str
    safety_script: str | None = None
    # Conditions that must hold for the trigger to fire. "No heat" is an
    # emergency at 10F and a routine call in June.
    require: dict[str, Any] = field(default_factory=dict)

    @property
    def safety_instruction(self) -> str | None:
        return SAFETY_SCRIPTS[self.safety_script] if self.safety_script else None


@dataclass(frozen=True, slots=True)
class Ruleset:
    trade: str
    version: int
    effective_from: date
    verified: bool
    triggers: tuple[Trigger, ...]
    required_capture: tuple[str, ...]
    never_say: tuple[str, ...]
    fixtures: tuple[str, ...]

    def by_code(self, code: str) -> Trigger | None:
        for trigger in self.triggers:
            if trigger.code == code:
                return trigger
        return None

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(t.code for t in self.triggers)


@dataclass(frozen=True, slots=True)
class Classification:
    """The answer. Deterministic, and the only thing that decides whether a
    phone rings at 2am."""

    trigger: str | None
    severity: Severity | None
    escalate: bool
    notify: Notify
    urgency: Urgency
    capture_gaps: tuple[str, ...]
    safety_script: str | None = None
    # How the trigger was arrived at: the model named a code, our phrase
    # matcher found one, or both agreed. Post-call QA compares the two.
    matched_by: str = "none"

    @property
    def safety_instruction(self) -> str | None:
        return SAFETY_SCRIPTS[self.safety_script] if self.safety_script else None


class RulesetError(ValueError):
    """A ruleset that would not be safe to run a call against."""
