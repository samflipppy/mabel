"""The owner's command grammar.

03-VOICE.md's table, parsed. A contractor standing on a roof with one hand free
types `WON RUIZ 3800`, not a sentence.

    1 2 3           expand item N from the last list
    WON RUIZ 3800   mark won, set value
    LOST CHEN       mark lost
    HENDERSON       that contact's thread summary
    FU              open follow-ups
    C               bridge a call to the last emergency caller
    anything else   LLM intent + RAG over that tenant's thread

The design rule, and it is the reason this is a deterministic parser rather
than a model call: **an LLM never sets a dollar figure.** `WON RUIZ 3800` is
the one place a job value enters the system, and it is parsed here, by code,
from a number a human typed. If this file did not exist and the fallback
handled everything, invariant 4 would be broken by the most-used feature in the
product.

Pure. Takes a string, returns an intent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from mabel_domain.money import Money, MoneyError, parse_owner_amount


class Intent(StrEnum):
    EXPAND = "expand"
    MARK_WON = "mark_won"
    MARK_LOST = "mark_lost"
    CONTACT_SUMMARY = "contact_summary"
    FOLLOWUPS = "followups"
    BRIDGE_CALL = "bridge_call"
    HELP = "help"
    STOP = "stop"
    # Not understood by the grammar. Handed to the recall layer.
    RECALL = "recall"


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    intent: Intent
    # The list index for EXPAND.
    index: int | None = None
    # The name fragment for WON / LOST / a bare surname.
    subject: str | None = None
    # Only ever set by MARK_WON, and only from digits a human typed.
    amount: Money | None = None
    raw: str = ""
    # Why a nearly-matching command was not taken, so the reply can say.
    note: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


# Carrier-mandated keywords. These are not ours to reinterpret: replying to
# STOP with anything other than compliance is an A2P violation.
STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
HELP_WORDS = {"HELP", "INFO", "?"}

# `1`, `2`, `3` — expand item N. Capped because the recap lists at most this
# many and a stray long number is more likely a phone number than an index.
MAX_LIST_INDEX = 9

_INDEX = re.compile(r"^([1-9])$")
_WON = re.compile(r"^WON\s+(.+?)(?:\s+(\S+))?$", re.I)
_LOST = re.compile(r"^LOST\s+(.+?)(?:\s+(.*))?$", re.I)
_BARE_NAME = re.compile(r"^[A-Za-z][A-Za-z'\-. ]{1,40}$")


def parse(message: str) -> ParsedCommand:
    """Parse one inbound SMS from an owner.

    Order matters. Carrier keywords first, because they must win against
    anything else. Then the shapes that carry consequences (won, lost), then
    the ambiguous ones.
    """
    raw = (message or "").strip()
    if not raw:
        return ParsedCommand(intent=Intent.RECALL, raw=raw)

    upper = raw.upper()

    if upper in STOP_WORDS:
        return ParsedCommand(intent=Intent.STOP, raw=raw)
    if upper in HELP_WORDS:
        return ParsedCommand(intent=Intent.HELP, raw=raw)

    index_match = _INDEX.match(raw)
    if index_match:
        return ParsedCommand(intent=Intent.EXPAND, index=int(index_match.group(1)), raw=raw)

    if upper == "FU":
        return ParsedCommand(intent=Intent.FOLLOWUPS, raw=raw)

    if upper == "C":
        # Bridging a call is an action with a consequence — it dials somebody.
        # The handler confirms before doing it; the grammar only recognises it.
        return ParsedCommand(intent=Intent.BRIDGE_CALL, raw=raw)

    won = _WON.match(raw)
    if won:
        return _parse_won(won, raw)

    lost = _LOST.match(raw)
    if lost:
        subject = lost.group(1).strip()
        reason = (lost.group(2) or "").strip() or None
        return ParsedCommand(
            intent=Intent.MARK_LOST, subject=subject, raw=raw, meta={"reason": reason}
        )

    if _BARE_NAME.match(raw) and len(raw.split()) <= 3:
        # A bare word that looks like a name. Ambiguous by nature — the handler
        # resolves it against contacts and falls through to recall if nothing
        # matches, rather than this file guessing.
        return ParsedCommand(intent=Intent.CONTACT_SUMMARY, subject=raw, raw=raw)

    return ParsedCommand(intent=Intent.RECALL, raw=raw)


def _parse_won(match: re.Match[str], raw: str) -> ParsedCommand:
    """`WON RUIZ 3800`, and every way it can go wrong.

    A misparsed amount here becomes the headline number on a monthly report the
    owner judges us by, so the failure mode is "ask him to repeat it", never
    "take the closest reading".
    """
    subject = match.group(1).strip()
    trailing = (match.group(2) or "").strip()

    if not trailing:
        # `WON RUIZ` with no figure. Legitimate — he may not know it yet — so
        # this is a valid command, and the handler asks for the value.
        return ParsedCommand(
            intent=Intent.MARK_WON,
            subject=subject,
            raw=raw,
            note="no amount given",
        )

    try:
        amount = parse_owner_amount(trailing)
    except MoneyError as exc:
        # The trailing token is not a number. Two readings: a two-word name
        # (`WON MARY BETH`) or a typo'd figure. Treat it as part of the name
        # and ask for the amount, which is recoverable either way.
        return ParsedCommand(
            intent=Intent.MARK_WON,
            subject=f"{subject} {trailing}".strip(),
            raw=raw,
            note=str(exc),
        )

    return ParsedCommand(intent=Intent.MARK_WON, subject=subject, amount=amount, raw=raw)


def is_carrier_keyword(message: str) -> bool:
    """STOP and HELP are the carrier's, not ours.

    Checked separately as well as in `parse`, because the inbound webhook has
    to honour them before any tenant resolution — an unsubscribe from a number
    we cannot place is still an unsubscribe.
    """
    upper = (message or "").strip().upper()
    return upper in STOP_WORDS or upper in HELP_WORDS
