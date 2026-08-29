"""`answer_question`.

The tool whose job is to say "I don't know".

03-VOICE.md: searches `knowledge_items`, returns the answer or `{found: false}`,
in which case Mabel says someone will follow up rather than guessing. That
second branch is the whole point. A language model asked "do you do drywall?"
will answer, confidently, whether or not it has any basis to — and a homeowner
told yes who is then told no by the contractor blames the contractor.

So this returns the owner's own words or nothing. There is no summarisation
step and no fallback to the model's general knowledge, because both are ways of
answering on a business's behalf without its permission.
"""

from __future__ import annotations

from typing import Any

from mabel_mcp.repo import ToolContext

# Longer than this and it is not a Q&A answer, it is a document. The portal
# caps the field; this is the belt to that braces.
MAX_ANSWER_CHARS = 600


async def answer_question(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    question = str(args.get("question", "")).strip()
    if not question:
        return _unknown()

    matches = await ctx.repo.search_knowledge(question)
    if not matches:
        return _unknown()

    best = matches[0]
    answer = str(best.get("answer", "")).strip()
    if not answer:
        return _unknown()

    return {
        "found": True,
        # Verbatim. Not paraphrased, not extended, not combined with a second
        # entry. It is the owner's sentence and she reads it.
        "answer": answer[:MAX_ANSWER_CHARS],
        "matched_question": best.get("question"),
    }


def _unknown() -> dict[str, Any]:
    return {
        "found": False,
        "answer": None,
        # Said plainly, so the prompt has something concrete to follow rather
        # than an absence to interpret.
        "guidance": "Say that you'll have someone follow up on that. Do not guess.",
    }
