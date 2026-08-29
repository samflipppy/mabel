"""`lookup_customer` and `get_job_history`.

What these are for: letting her open with "Hi Mrs. Henderson — is this about
the exterior job?" instead of asking a fifteen-year customer to spell her name.
That moment is most of why the product feels different from an answering
service.

What they must not do: hand the model a dollar figure. `get_job_history`
returns what the job *was*, never what it was *worth*. The value is in the
database, the portal shows it, and the voice agent never sees it — because
anything the model can see it can say, and a job value read aloud is a quote in
the caller's ears whatever we intended.
"""

from __future__ import annotations

from typing import Any

from mabel_domain.phone import PhoneError, normalize_e164
from mabel_mcp.repo import ToolContext

# A caller does not need last decade's work read back at them.
DEFAULT_HISTORY_LIMIT = 3
MAX_HISTORY_LIMIT = 10


async def lookup_customer(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """`{found, name, last_job, last_job_date, open_balance}` per 03-VOICE.md.

    `open_balance` is in the contract and is always `None`. There is no
    invoicing, payments, or balance anywhere in 01-SCHEMA.sql, so there is
    nothing to compute it from — see docs/BLOCKED.md S5. Returning the key with
    a null is better than dropping it: the contract stays as specified, and the
    day an invoicing table exists this fills in without the tool changing shape.

    Had there been a source, this would still be the wrong place to send it.
    """
    phone = args.get("phone")
    address = args.get("address")

    if not phone and not address:
        # The schema's anyOf says one of them is required. Belt and braces,
        # because a malformed tool call should get a usable answer rather than
        # an exception mid-conversation.
        return _not_found("no phone or address given")

    if not phone:
        # Address-only lookup is in the schema but has no index behind it and
        # no deterministic match rule. Saying so plainly beats a fuzzy guess
        # that greets the wrong person by name.
        return _not_found("address-only lookup is not supported")

    try:
        normalized = normalize_e164(str(phone))
    except PhoneError:
        return _not_found("phone number was not usable")

    contact = await ctx.repo.find_contact_by_phone(normalized)
    if contact is None:
        return _not_found()

    last = await ctx.repo.last_job(contact["id"])
    return {
        "found": True,
        "name": contact.get("display_name"),
        "last_job": last["job_type"] if last else None,
        "last_job_date": last["when"].date().isoformat() if last and last.get("when") else None,
        # See the docstring. Null by design, not by omission.
        "open_balance": None,
    }


def _not_found(reason: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "found": False,
        "name": None,
        "last_job": None,
        "last_job_date": None,
        "open_balance": None,
    }
    if reason:
        result["reason"] = reason
    return result


async def get_job_history(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Past jobs, so she can pick up a thread. No values, ever."""
    try:
        normalized = normalize_e164(str(args.get("phone", "")))
    except PhoneError:
        return {"found": False, "jobs": []}

    contact = await ctx.repo.find_contact_by_phone(normalized)
    if contact is None:
        return {"found": False, "jobs": []}

    requested = args.get("limit", DEFAULT_HISTORY_LIMIT)
    limit = (
        requested
        if isinstance(requested, int) and not isinstance(requested, bool)
        else DEFAULT_HISTORY_LIMIT
    )
    limit = max(1, min(limit, MAX_HISTORY_LIMIT))

    rows = await ctx.repo.job_history(contact["id"], limit=limit)
    return {
        "found": True,
        "jobs": [
            {
                # No `value_cents`, and note there is nowhere here it could
                # come from: DbRepo.job_history already drops it on the way out
                # of the query layer. Two gates rather than one, because this
                # is the field that turns a history lookup into a quote.
                "job_type": row.get("job_type"),
                "status": row.get("status"),
                "when": row["created_at"].date().isoformat() if row.get("created_at") else None,
            }
            for row in rows
        ],
    }
