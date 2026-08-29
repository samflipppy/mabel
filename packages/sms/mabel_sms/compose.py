"""Building the messages an owner actually receives.

Everything here is GSM-7 and capped at 160 characters. Not out of thrift — a
message that splits into three segments arrives out of order often enough to
matter, and the one that matters is the 3am emergency.

**Every dollar figure in this file comes from an integer cents column.**
Formatting happens through `Money.format_whole()`, which is deterministic code
reading a number a human entered. No LLM output reaches any of these builders,
and the recall layer, which does involve a model, is forbidden from producing
figures at all.

Pure. Takes rows, returns strings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from mabel_domain.money import Money
from mabel_domain.phone import format_national

# One GSM-7 segment. Going over is allowed where the content genuinely needs it
# (the morning recap), but never for an emergency.
SEGMENT = 160

# GSM-7 has no emoji, no curly quotes, no em dashes. A single unsupported
# character silently switches the whole message to UCS-2 and halves the segment
# length, which is how a 158-character message becomes three parts.
_GSM7_SUBSTITUTIONS = {
    "’": "'",
    "‘": "'",
    "“": '"',
    "”": '"',
    "—": "-",
    "–": "-",
    "…": "...",
    " ": " ",
}

_NON_GSM7 = re.compile(
    r"[^\r\n A-Za-z0-9@£$¥èéùìòÇØøÅåÆæßÉ!\"#¤%&'()*+,\-./:;<=>?¡ÄÖÑÜ§¿äöñüà_^{}\\\[~\]|€]"
)


def to_gsm7(text: str) -> str:
    """Make a string safe for one segment's worth of GSM-7.

    Substitutes the characters a phone keyboard or a copy-paste introduces,
    then strips anything still outside the alphabet. Stripping rather than
    replacing with '?' because a name with an accent reads better shortened
    than peppered with question marks.
    """
    for bad, good in _GSM7_SUBSTITUTIONS.items():
        text = text.replace(bad, good)
    return _NON_GSM7.sub("", text)


ELLIPSIS = "..."


def fit(text: str, limit: int = SEGMENT) -> str:
    """Trim to one segment, on a word boundary where possible.

    Three dots rather than a single ellipsis character: U+2026 is not in
    GSM-7, and one character outside the alphabet switches the whole message
    to UCS-2 and halves the segment length.
    """
    text = to_gsm7(text).strip()
    if len(text) <= limit:
        return text

    cut = text[: limit - len(ELLIPSIS)]
    # Prefer a word boundary, but only if one exists in the back half. A very
    # long single token should be cut rather than reduced to nothing.
    if " " in cut[limit // 2 :]:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" .,-") + ELLIPSIS


@dataclass(frozen=True, slots=True)
class RecapLead:
    """One row in the morning recap. Money is cents, or absent."""

    name: str | None
    job_type: str | None
    urgency: str
    phone_e164: str | None
    at: datetime
    value_cents: int | None = None


def morning_recap(
    *,
    business_name: str,
    leads: list[RecapLead],
    emergencies: int,
    calls_answered: int,
    local_day: str,
) -> str:
    """The 7am text. The one Sam's own phone has to find useful.

    Structure: what happened, then what needs him, then how to act. He reads
    the first line walking to the truck and the rest at a light.

    Deliberately not chatty. "Mabel answered 4 calls" is a fact; "Good morning!
    Mabel had a busy night!" is noise he learns to skip.
    """
    if not leads and not calls_answered:
        # Silence is information too, and saying nothing at all makes him
        # wonder whether the forwarding broke.
        return to_gsm7(f"{local_day}: no calls overnight.")

    header = f"{local_day}: {calls_answered} call{'s' if calls_answered != 1 else ''}"
    if emergencies:
        header += f", {emergencies} emergency" + ("" if emergencies == 1 else " calls")
    header += f", {len(leads)} lead{'s' if len(leads) != 1 else ''}."

    lines = [header]
    for position, lead in enumerate(leads[:3], start=1):
        marker = "!" if lead.urgency == "emergency" else ""
        who = lead.name or "Unknown caller"
        what = lead.job_type or "no detail"
        lines.append(f"{position}{marker} {who} - {what}")

    if len(leads) > 3:
        lines.append(f"+{len(leads) - 3} more in the portal.")

    lines.append("Reply 1-3 for details, FU for follow-ups.")
    return to_gsm7("\n".join(lines))


def lead_detail(lead: RecapLead) -> str:
    """What `1` expands to. Everything he needs to make the call."""
    parts = [lead.name or "Unknown caller"]
    if lead.job_type:
        parts.append(lead.job_type)
    if lead.phone_e164:
        parts.append(format_national(lead.phone_e164))
    if lead.value_cents is not None:
        # From the cents column. He typed it, so he can see it.
        parts.append(Money(lead.value_cents).format_whole())
    parts.append(
        lead.at.strftime("%a %-I:%M%p").lower()
        if _supports_dash_l()
        else lead.at.strftime("%a %H:%M")
    )
    return fit(" - ".join(parts))


def _supports_dash_l() -> bool:
    """`%-I` is a glibc extension and is not available on Windows.

    Worth handling rather than ignoring: the tests run on both, and a
    ValueError formatting a timestamp would take out the whole recap job.
    """
    try:
        datetime(2026, 1, 1, 9, 0).strftime("%-I")
    except ValueError:
        return False
    return True


def won_confirmation(*, name: str, amount: Money | None) -> str:
    """The reply to `WON RUIZ 3800`.

    Reads the figure back. He typed it into a phone keyboard one-handed, and a
    transposed digit here is a wrong number on the monthly report.
    """
    if amount is None:
        return fit(f"Marked {name} won. Reply with the value when you have it.")
    return fit(f"{name} marked won at {amount.format_whole()}.")


def lost_confirmation(*, name: str, reason: str | None) -> str:
    if reason:
        return fit(f"Marked {name} lost: {reason}.")
    return fit(f"Marked {name} lost.")


def followups(leads: list[RecapLead], *, local_now: datetime) -> str:
    """What `FU` returns. Oldest first, because that is the one going cold."""
    if not leads:
        return "Nothing waiting on you. All leads have been touched."

    lines = ["Waiting on you:"]
    for position, lead in enumerate(leads[:3], start=1):
        days = max(0, (local_now.date() - lead.at.date()).days)
        age = "today" if days == 0 else f"{days}d"
        lines.append(
            f"{position} {lead.name or 'Unknown'} - {lead.job_type or 'no detail'} ({age})"
        )
    if len(leads) > 3:
        lines.append(f"+{len(leads) - 3} more.")
    return to_gsm7("\n".join(lines))


def weekly_summary(
    *,
    calls_answered: int,
    leads_created: int,
    emergencies: int,
    jobs_won: int,
    won_value_cents: int,
) -> str:
    """Monday morning. The only outbound message that carries a total, and it
    is a sum of integer cents the owner entered by hand."""
    total = Money(won_value_cents)
    lines = [
        f"Last week: {calls_answered} calls, {leads_created} leads, {emergencies} emergencies.",
    ]
    if jobs_won:
        lines.append(f"You marked {jobs_won} won: {total.format_whole()}.")
    else:
        # Saying so plainly. A summary that is always good news gets ignored.
        lines.append("No jobs marked won yet.")
    return to_gsm7(" ".join(lines))


def silence_alert(*, business_name: str, days_quiet: int) -> str:
    """The churn catcher. A tenant who used to get calls and now gets none has
    almost certainly broken their forwarding, and they will blame us before
    they check."""
    return fit(
        f"No calls have reached Mabel for {days_quiet} days. "
        "That usually means call forwarding got switched off. "
        "Check your phone settings or reply HELP."
    )


def followup_nudge(lead: RecapLead, *, hours: int) -> str:
    who = lead.name or "a caller"
    what = lead.job_type or "a job"
    phone = format_national(lead.phone_e164) if lead.phone_e164 else ""
    return fit(f"{who} ({what}) has been waiting {hours}h. {phone}".strip())


def help_message(*, business_name: str) -> str:
    """Carrier-required, and genuinely useful. Lists the grammar."""
    return fit(
        "Mabel: 1-3 details, FU follow-ups, WON <name> <value>, "
        "LOST <name>, C call back last emergency. STOP to opt out."
    )


def stop_confirmation() -> str:
    """Compliance. Short, unambiguous, no attempt to talk them out of it."""
    return "You will not receive further messages from Mabel. Reply START to resume."
