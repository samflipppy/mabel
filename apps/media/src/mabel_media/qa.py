"""Post-call QA. Did she do the job properly?

03-VOICE.md names four checks: did she quote a price, miss an emergency,
escalate a non-emergency at 2am, or lose the caller under twenty seconds. Each
one flags `calls.qa_flags`, and the portal surfaces flagged calls on the
dashboard's "needs you" list.

The point is not to grade a model. It is that these four failures are invisible
otherwise — a contractor does not listen to his own call recordings, so a Mabel
who started quoting prices last Tuesday would go unnoticed until a customer
argued about an invoice.

Pure. Takes a transcript and a classification, returns flags. No I/O, so every
branch is cheap to fixture.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from mabel_domain.enums import QaFlag
from mabel_domain.hours import to_tenant_local

# A call that ended this fast did not collect anything useful. Either she was
# hung up on or something went wrong at the start.
LOST_CALLER_SECONDS = 20

# The hours where an unnecessary escalation is most expensive. Waking a
# contractor at 2pm for a slow drain is a nuisance; at 2am it is why he cancels.
SMALL_HOURS = range(0, 6)

# Money she may have said. Deliberately broader than the prompt's own check,
# because this runs over a transcript rather than over text we assembled.
_SPOKEN_MONEY = re.compile(
    r"[$£€]\s?\d"
    r"|\b\d{2,5}\s*(?:dollars|bucks|pounds)\b"
    r"|\b(?:about|around|roughly|typically|usually|starts? at|somewhere around)\s+"
    r"(?:[$£€]\s?)?\d{2,5}\b",
    re.I,
)

# An arrival promise with a clock time in it.
_PROMISED_ARRIVAL = re.compile(
    r"\b(?:be there|arrive|out to you|with you|on (?:his|her|their) way)\b"
    r"[^.?!]{0,40}\b(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm|o'clock)\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class QaInputs:
    duration_sec: int
    started_at: datetime
    timezone: str
    assistant_text: str
    # What the deterministic backstop concluded, independently of the model.
    backstop_escalates: bool
    # What actually happened on the call.
    escalated: bool
    booked_a_slot: bool
    capture_gaps: tuple[str, ...] = ()


def review(inputs: QaInputs) -> list[str]:
    """Return the flags for this call. Empty is the good case."""
    flags: list[str] = []

    if _quoted_a_price(inputs.assistant_text):
        flags.append(QaFlag.QUOTED_PRICE.value)

    if _promised_an_arrival(inputs.assistant_text, booked=inputs.booked_a_slot):
        flags.append(QaFlag.PROMISED_ARRIVAL.value)

    if inputs.backstop_escalates and not inputs.escalated:
        # The one that matters most. The phrase backstop caught something the
        # model did not, and nobody's phone rang.
        flags.append(QaFlag.MISSED_EMERGENCY.value)

    if _over_escalated(inputs):
        flags.append(QaFlag.OVER_ESCALATED.value)

    if inputs.duration_sec < LOST_CALLER_SECONDS:
        flags.append(QaFlag.LOST_CALLER_EARLY.value)

    if inputs.capture_gaps and inputs.duration_sec >= LOST_CALLER_SECONDS:
        # Only worth flagging on a call that ran long enough to have collected
        # things. A ten-second hangup has gaps by definition.
        flags.append(QaFlag.CAPTURE_INCOMPLETE.value)

    return flags


def _quoted_a_price(assistant_text: str) -> bool:
    """Invariant 4, checked against what she actually said.

    Every other guard in the system stops a price reaching her. This is the one
    that notices if one got through anyway.
    """
    return bool(_SPOKEN_MONEY.search(assistant_text or ""))


def _promised_an_arrival(assistant_text: str, *, booked: bool) -> bool:
    """ "Never promise an arrival time not returned by check_availability."

    A booked slot means a time was legitimately agreed, so a clock time in the
    transcript is expected. Without one, a specific hour is a promise nobody
    can keep.
    """
    if booked:
        return False
    return bool(_PROMISED_ARRIVAL.search(assistant_text or ""))


def _over_escalated(inputs: QaInputs) -> bool:
    """Woke somebody in the small hours for something the ruleset says is not
    an emergency.

    Only flagged overnight. During the day an over-eager escalation costs a
    phone call; at 3am it costs the account.
    """
    if not inputs.escalated or inputs.backstop_escalates:
        return False
    local_hour = to_tenant_local(inputs.started_at, inputs.timezone).hour
    return local_hour in SMALL_HOURS


def assistant_text_from_turns(turns: list[dict[str, Any]]) -> str:
    """Everything Mabel said, as one string.

    Only her turns. The caller may well say "so what's this going to cost me,
    two hundred?" and that is not her quoting a price — flagging it would train
    everyone to ignore the flag.
    """
    return " ".join(
        str(turn.get("text", ""))
        for turn in turns
        if str(turn.get("role", "")).lower() in {"assistant", "mabel"}
    )


def summarise(flags: list[str]) -> str | None:
    """One line for the dashboard's needs-you row."""
    if not flags:
        return None
    wording = {
        QaFlag.QUOTED_PRICE.value: "may have quoted a price",
        QaFlag.MISSED_EMERGENCY.value: "may have missed an emergency",
        QaFlag.OVER_ESCALATED.value: "woke someone overnight for a routine call",
        QaFlag.LOST_CALLER_EARLY.value: "caller hung up almost immediately",
        QaFlag.PROMISED_ARRIVAL.value: "may have promised an arrival time",
        QaFlag.CAPTURE_INCOMPLETE.value: "did not collect everything",
    }
    return "; ".join(wording.get(flag, flag) for flag in flags)
