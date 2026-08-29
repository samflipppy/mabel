"""`create_lead`, `escalate_emergency`, `log_note`.

The three tools that write. Between them they are the product: a call that
would have gone to voicemail becomes a row the owner sees at 7am, or a phone
ringing at 2am when it should.

Two things are true of all three and worth stating once.

**Nothing here writes money.** `leads.value_cents` is owner-entered and this
path never touches it. Everything these tools record came from a caller through
a language model, and that is exactly the material invariant 4 keeps away from
a money column.

**The emergency SMS and the lead are written in one transaction.** The
dispatcher holds the transaction open across the handler, so either the owner
gets a text about a lead that exists, or neither happened. A text about a lead
that was rolled back is a contractor woken at 3am for a record he cannot find.
"""

from __future__ import annotations

from typing import Any

from mabel_domain.phone import PhoneError, format_national, normalize_e164
from mabel_mcp.repo import ToolContext

VALID_URGENCY = {"routine", "soon", "emergency"}

# An SMS the owner reads half asleep. GSM-7, one segment where possible.
EMERGENCY_SMS_LIMIT = 160


async def create_lead(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("name", "")).strip() or None
    job_type = str(args.get("job_type", "")).strip() or None

    try:
        phone = normalize_e164(str(args.get("phone", "")))
    except PhoneError:
        # A lead with an unusable callback number is a lead the owner cannot
        # act on. Better she asks again than we store a dead number.
        return {"created": False, "reason": "callback number was not usable"}

    urgency = str(args.get("urgency", "routine")).strip().lower()
    if urgency not in VALID_URGENCY:
        # The model returned something outside the enum. Default down, never
        # up: an over-called emergency wakes someone for nothing, and doing
        # that on a parse error rather than on evidence is how trust goes.
        urgency = "routine"

    contact_id, _how = await ctx.repo.resolve_or_create_contact(phone_e164=phone, name=name)

    lead_id = await ctx.repo.create_lead(
        contact_id=contact_id,
        caller_name=name,
        callback_e164=phone,
        service_address=str(args.get("address", "")).strip() or None,
        job_type=job_type,
        description=str(args.get("description", "")).strip() or None,
        urgency=urgency,
        source=str(args.get("source", "")).strip() or None,
    )

    await ctx.repo.record_event(
        contact_id=contact_id,
        lead_id=lead_id,
        kind="call",
        direction="inbound",
        occurred_at=ctx.now,
        body=str(args.get("description", "")).strip() or None,
        payload={"call_id": ctx.call_id, "job_type": job_type, "urgency": urgency},
    )

    return {"created": True, "lead_id": str(lead_id), "urgency": urgency}


async def escalate_emergency(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Wake somebody. Also creates the lead — 03-VOICE.md, and it matters:
    an emergency that produced a phone call but no record is one the office
    manager cannot find in the morning.

    Returns whether anyone was actually reached, because Mabel has to say
    different things in the two cases. With nobody on call she must not imply a
    truck is moving.
    """
    name = str(args.get("name", "")).strip() or None
    nature = str(args.get("nature", "")).strip() or None

    try:
        phone = normalize_e164(str(args.get("phone", "")))
    except PhoneError:
        return {"escalated": False, "reason": "callback number was not usable"}

    address = str(args.get("address", "")).strip() or None
    caller_is_safe = args.get("caller_is_safe")

    contact_id, _how = await ctx.repo.resolve_or_create_contact(phone_e164=phone, name=name)

    lead_id = await ctx.repo.create_lead(
        contact_id=contact_id,
        caller_name=name,
        callback_e164=phone,
        service_address=address,
        job_type=nature,
        description=nature,
        urgency="emergency",
        source=str(args.get("source", "")).strip() or None,
        escalated_at=ctx.now,
    )

    body = compose_emergency_sms(
        name=name, phone=phone, address=address, nature=nature, caller_is_safe=caller_is_safe
    )
    reached = await ctx.repo.notify_oncall(body=body, lead_id=lead_id)

    await ctx.repo.record_event(
        contact_id=contact_id,
        lead_id=lead_id,
        kind="call",
        direction="inbound",
        occurred_at=ctx.now,
        body=nature,
        payload={
            "call_id": ctx.call_id,
            "escalated": True,
            "oncall_reached": reached,
            "caller_is_safe": caller_is_safe,
        },
    )

    return {
        "escalated": True,
        "lead_id": str(lead_id),
        # She reads this. True: "someone will call you right back." False:
        # "I've flagged this as urgent and someone will call you back."
        "oncall_reached": reached,
    }


def compose_emergency_sms(
    *,
    name: str | None,
    phone: str,
    address: str | None,
    nature: str | None,
    caller_is_safe: bool | None,
) -> str:
    """The text an owner reads at 3am, one thumb, half asleep.

    Ordered by what he needs first: that it is an emergency, who, where, what,
    and the number to call. Truncated to one SMS segment — a message split into
    three arrives out of order often enough to matter.

    Pure, so it can be tested without a database. No dollar figures, and there
    is nothing here one could come from.
    """
    parts = ["EMERGENCY"]
    if name:
        parts.append(name)
    if address:
        parts.append(address)
    if nature:
        parts.append(nature)
    if caller_is_safe is False:
        # Only when explicitly false. Unknown is not the same as unsafe, and
        # crying wolf here devalues the words.
        parts.append("CALLER NOT SAFE")
    parts.append(format_national(phone))

    body = " - ".join(parts)
    if len(body) <= EMERGENCY_SMS_LIMIT:
        return body

    # Trim the free-text description, never the phone number. He can act on a
    # number without a description; he can do nothing with a description and no
    # number.
    tail = f" - {format_national(phone)}"
    head = body[: EMERGENCY_SMS_LIMIT - len(tail) - 1].rstrip(" -")
    return head + tail


async def log_note(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Anything the caller said that does not fit a field.

    Lands in the thread, not on the lead, because it is a thing that was said
    rather than a property of the job.
    """
    note = str(args.get("note", "")).strip()
    if not note:
        return {"logged": False, "reason": "empty note"}

    await ctx.repo.record_event(
        kind="note",
        direction="inbound",
        occurred_at=ctx.now,
        body=note,
        payload={"call_id": ctx.call_id, "source": "voice_agent"},
    )
    return {"logged": True}
