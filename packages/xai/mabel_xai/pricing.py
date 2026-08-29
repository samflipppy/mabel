"""What a call cost us. Integer cents, deterministic, and internal.

This is a cost figure for margin tracking. It is **never** shown to a customer
and never becomes a number in a report they read. Invariant 4 is about dollar
figures reaching a customer; the only ones that do are the job values the owner
typed in himself.

Every rate here is published and cited in `docs/xai_notes.md`. No LLM output
touches this module — it takes a duration in seconds and multiplies.
"""

from __future__ import annotations

import math

# VERIFIED, docs.x.ai pricing 2026-08-29.
VOICE_CENTS_PER_MINUTE = 8  # $0.08/min for grok-voice-think-fast-2.0
CONVERSATION_ITEM_CENTS = 0.4  # $0.004 per extra conversation.item.create

# The 1.0 rate, here only so a reconciliation can tell which model was billed.
# Never use the alias that would select it.
LEGACY_VOICE_CENTS_PER_MINUTE = 5


class PricingError(ValueError):
    pass


def voice_cost_cents(duration_sec: int) -> int:
    """Cost of the speech-to-speech minutes.

    # ASSUMPTION (docs/xai_notes.md A9): the rate is published, the rounding is
    # not. We round the whole call up to the next cent, which is the direction
    # that cannot understate our costs. Reconcile against the first invoice.
    """
    if isinstance(duration_sec, bool) or not isinstance(duration_sec, int):
        raise PricingError(f"duration must be whole seconds, got {type(duration_sec).__name__}")
    if duration_sec < 0:
        raise PricingError(f"negative duration: {duration_sec}")
    return math.ceil(duration_sec * VOICE_CENTS_PER_MINUTE / 60)


def conversation_item_cost_cents(count: int) -> int:
    """Extra `conversation.item.create` text items, at $0.004 each.

    The opening disclosure is one of these on every call, so this is never
    zero on a real call.
    """
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise PricingError(f"item count must be a non-negative integer, got {count!r}")
    return math.ceil(count * CONVERSATION_ITEM_CENTS)


def call_cost_cents(*, duration_sec: int, conversation_items: int = 1) -> int:
    """Total xAI cost for one call, in integer cents."""
    return voice_cost_cents(duration_sec) + conversation_item_cost_cents(conversation_items)


def minutes_from_seconds(duration_sec: int) -> float:
    """For `usage_daily.voice_minutes`, which is numeric(10,2) — minutes, not
    money. Rounded to two places to match the column."""
    if duration_sec < 0:
        raise PricingError(f"negative duration: {duration_sec}")
    return round(duration_sec / 60, 2)
