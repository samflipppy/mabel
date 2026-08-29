"""`check_availability` and `book_estimate`.

The rule these two exist to enforce: **Mabel never invents a time.**

A homeowner told "someone will be there between eight and twelve tomorrow" has
rearranged their morning. If that window came from a language model rather than
a calendar, the contractor finds out when the customer phones at half past
twelve, angry, and the answering service is the reason.

So the mechanism is shape, not instruction. `check_availability` returns
windows with opaque `slot_id`s. `book_estimate` takes a `slot_id` and looks it
up. There is no free-text time field anywhere in either schema. She cannot book
a window she was not offered, because there is no argument in which to express
one.
"""

from __future__ import annotations

from typing import Any

from mabel_domain.phone import PhoneError, normalize_e164
from mabel_mcp.repo import ToolContext

# Three is enough to choose from and few enough to say out loud.
MAX_OFFERED = 3


async def check_availability(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Real windows, or an honest empty list.

    An empty list is a valid answer and the prompt tells her what to do with
    it: take the details and say someone will call back to arrange a time.
    Inventing one to be helpful is the failure mode.
    """
    job_type = str(args.get("job_type", "")).strip()
    if not job_type:
        return {"slots": [], "reason": "no job type given"}

    offered = await ctx.repo.available_slots(job_type=job_type)

    preferred = str(args.get("preferred_window", "")).strip().lower()
    if preferred:
        # A caller who asked for mornings gets mornings first. This only
        # reorders what the calendar already offered; it never adds to it.
        offered = sorted(offered, key=lambda s: preferred not in str(s.get("spoken", "")).lower())

    return {
        "slots": [
            {
                "slot_id": slot["slot_id"],
                # `spoken` is what she reads out: "Tuesday morning". Coarse on
                # purpose. A window a contractor can keep, not a precise time
                # he cannot.
                "spoken": slot["spoken"],
                "day": slot["day"],
                "label": slot["label"],
            }
            for slot in offered[:MAX_OFFERED]
        ]
    }


async def book_estimate(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Book one of the offered windows.

    Returns `{booked: false}` when the slot id is not one we offered — which
    includes the case where the model made one up, and the case where somebody
    else took it in the seconds since.
    """
    slot_id = str(args.get("slot_id", "")).strip()
    if not slot_id:
        return {"booked": False, "reason": "no slot chosen"}

    name = str(args.get("name", "")).strip() or None
    try:
        phone = normalize_e164(str(args.get("phone", "")))
    except PhoneError:
        return {"booked": False, "reason": "callback number was not usable"}

    contact_id, _how = await ctx.repo.resolve_or_create_contact(phone_e164=phone, name=name)

    booked = await ctx.repo.book_slot(slot_id=slot_id, contact_id=contact_id, lead_id=None)
    if not booked:
        return {
            "booked": False,
            # Phrased for her to relay. She should re-offer, not apologise for
            # a system she cannot explain.
            "reason": "that window is no longer available",
        }

    await ctx.repo.record_event(
        contact_id=contact_id,
        kind="system",
        direction="internal",
        occurred_at=ctx.now,
        body="Estimate window booked during an after-hours call.",
        payload={"slot_id": slot_id, "call_id": ctx.call_id},
    )
    return {"booked": True, "slot_id": slot_id}
