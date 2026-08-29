"""The owner's SMS interface.

Three modules, one rule between them: no LLM output ever becomes a dollar
figure. `intents.py` parses `WON RUIZ 3800` deterministically, because that is
the one place a job value enters the system. `compose.py` formats figures from
integer cents columns. `recall.py`, the only part that involves a model, has
money stripped from its inputs and checked out of its outputs.
"""

from __future__ import annotations

from mabel_sms.compose import (
    SEGMENT,
    RecapLead,
    fit,
    followup_nudge,
    followups,
    help_message,
    lead_detail,
    lost_confirmation,
    morning_recap,
    silence_alert,
    stop_confirmation,
    to_gsm7,
    weekly_summary,
    won_confirmation,
)
from mabel_sms.intents import Intent, ParsedCommand, is_carrier_keyword, parse
from mabel_sms.recall import (
    RecallRefused,
    RecallRow,
    build_prompt,
    no_records_reply,
    safe_answer,
    strip_money,
    to_rows,
    validate_answer,
)

__all__ = [
    "SEGMENT",
    "Intent",
    "ParsedCommand",
    "RecallRefused",
    "RecallRow",
    "RecapLead",
    "build_prompt",
    "fit",
    "followup_nudge",
    "followups",
    "help_message",
    "is_carrier_keyword",
    "lead_detail",
    "lost_confirmation",
    "morning_recap",
    "no_records_reply",
    "parse",
    "safe_answer",
    "silence_alert",
    "stop_confirmation",
    "strip_money",
    "to_gsm7",
    "to_rows",
    "validate_answer",
    "weekly_summary",
    "won_confirmation",
]
