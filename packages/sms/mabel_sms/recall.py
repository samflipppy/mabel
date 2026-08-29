"""The fallback: a question the grammar did not recognise.

"Did that guy from Detroit Ave ever call back?" is not a command, and a
contractor should not have to learn a syntax to ask it. So anything the grammar
does not match comes here: retrieve from that tenant's thread, then compose an
answer from the retrieved rows.

Three constraints shape everything below.

**No dollar figure may come out.** A model composing over rows that include
job values will read one out, and that is an LLM output becoming a dollar
figure — invariant 4. So the rows handed to the model have money removed
before it sees them, and the answer is checked again on the way out. Two gates,
because this is the one place in the product where a model writes text a
customer's owner will act on.

**Retrieval is tenant-scoped by construction.** The rows arrive already
fetched through `tenant_scope()`. This module never queries; it cannot reach
the wrong tenant because it cannot reach a database at all.

**The answer is grounded or it is refused.** With no rows retrieved, the reply
says so. A model asked "did he call back?" with no context will produce a
plausible yes.

Pure. Takes rows and a question, returns a prompt and validates an answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from mabel_sms.compose import SEGMENT, fit

# Keys that carry money and are stripped before the model sees a row.
MONEY_KEYS = frozenset(
    {"value_cents", "value", "amount", "price", "cost", "total", "won_value_cents"}
)

# Anything money-shaped in a composed answer.
_MONEY_OUT = re.compile(r"[$£€]\s?\d|\b\d[\d,]{1,8}(?:\.\d{2})?\s*(?:dollars|bucks)\b", re.I)

# Enough context to answer, few enough to fit a model call that stays cheap.
MAX_ROWS = 12


class RecallRefused(ValueError):
    """The composed answer is not safe to send."""


@dataclass(frozen=True, slots=True)
class RecallRow:
    """One retrieved thread event, already stripped of money."""

    kind: str
    occurred_at: datetime
    who: str | None
    body: str | None
    direction: str | None = None


def strip_money(row: dict[str, Any]) -> dict[str, Any]:
    """Remove every money-carrying key before a model sees the row.

    Not redaction for privacy — the owner is entitled to his own numbers. It is
    that a model given a number will use it, and a number in a composed
    sentence is a figure nobody computed deterministically.
    """
    return {key: value for key, value in row.items() if key.lower() not in MONEY_KEYS}


def to_rows(events: list[dict[str, Any]]) -> list[RecallRow]:
    """Thread events into the shape the prompt renders. Money never survives."""
    rows: list[RecallRow] = []
    for event in events[:MAX_ROWS]:
        clean = strip_money(event)
        rows.append(
            RecallRow(
                kind=str(clean.get("kind", "note")),
                occurred_at=clean["occurred_at"],
                who=clean.get("who") or clean.get("display_name"),
                body=(str(clean["body"]) if clean.get("body") else None),
                direction=clean.get("direction"),
            )
        )
    return rows


def build_prompt(question: str, rows: list[RecallRow], *, business_name: str) -> str:
    """The prompt for the recall model.

    The instructions are blunt about the two failure modes: inventing an answer
    when the rows do not contain one, and mentioning money. Both are stated as
    prohibitions rather than preferences, because a hedged instruction in a
    160-character-answer task gets dropped.
    """
    if not rows:
        context = "(no matching records)"
    else:
        context = "\n".join(
            f"- {row.occurred_at:%b %d}: [{row.kind}] "
            f"{(row.who + ': ') if row.who else ''}{row.body or '(no text)'}"
            for row in rows
        )

    return (
        f"You are answering a question from the owner of {business_name} about his own "
        f"records. Answer only from the records below.\n\n"
        f"Records:\n{context}\n\n"
        f"Question: {question}\n\n"
        f"Rules:\n"
        f"- Answer in one sentence, under {SEGMENT} characters.\n"
        f"- If the records do not answer it, say so. Do not guess or infer.\n"
        f"- Never mention an amount of money, a price, or what a job is worth.\n"
        f"- No emoji, no exclamation marks.\n"
    )


def validate_answer(answer: str) -> str:
    """The second gate. Checks what the model produced before it is sent.

    Refuses rather than redacts. A sentence with the figure cut out of it
    ("the job was worth ") is worse than an honest "I can't answer that from
    the records" — it reads like a bug, and the owner cannot tell what was
    removed.
    """
    text = (answer or "").strip()
    if not text:
        raise RecallRefused("the model returned nothing")

    found = _MONEY_OUT.findall(text)
    if found:
        raise RecallRefused(
            f"the composed answer contains a figure: {found[:2]}. No LLM output "
            "becomes a dollar figure, so this answer is dropped rather than trimmed."
        )
    return fit(text)


def no_records_reply(question: str) -> str:
    """What to send when retrieval came back empty.

    Sent without calling a model at all. A model asked 'did he call back?' with
    no context will produce a plausible yes, and a plausible yes is worse than
    an honest nothing.
    """
    del question
    return fit(
        "I couldn't find anything about that in your records. Try a name, or check the portal."
    )


def safe_answer(*, question: str, rows: list[RecallRow], model_answer: str | None) -> str:
    """The whole path, so a caller cannot skip the gates by accident.

    With no rows there is no model call. With an answer that mentions money,
    the refusal is what gets sent.
    """
    if not rows:
        return no_records_reply(question)
    if model_answer is None:
        return no_records_reply(question)
    try:
        return validate_answer(model_answer)
    except RecallRefused:
        return fit(
            "I can't answer that one over text. It's all in the portal under "
            "that customer's thread."
        )
