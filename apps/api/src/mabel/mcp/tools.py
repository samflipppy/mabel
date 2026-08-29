"""Eight MCP tools. Only four write, and only to the token's tenant."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any
from uuid import UUID, uuid4

from mabel_verticals.evaluate import evaluate_scenario
from mabel_verticals.load import load_latest_rules

from mabel.leads.memory import Lead, Note, Store, reset_memory_store
from mabel.leads.memory import store as memory_store
from mabel.leads.persist import persist_lead, persist_note, using_database
from mabel.shops.packet import PacketError, normalize_zip, packet_for, reset_packets
from mabel.sms.notify import REASON_RECAP, reset_sms, send_owner_emergency_sms
from mabel.sms.recap import queue_morning_recap, reset_recap

READ_TOOLS = (
    "lookup_customer",
    "get_service_area",
    "check_availability",
    "get_job_history",
)
WRITE_TOOLS = (
    "create_lead",
    "escalate_emergency",
    "book_estimate",
    "log_note",
)
TOOL_NAMES = READ_TOOLS + WRITE_TOOLS

_current_tenant: ContextVar[UUID] = ContextVar("mabel_mcp_tenant")


class ToolError(ValueError):
    pass


def current_tenant() -> UUID:
    try:
        return _current_tenant.get()
    except LookupError as exc:
        raise ToolError("Mabel will not run a tool without a tenant token.") from exc


def bind_tenant(tenant_id: UUID):
    return _current_tenant.set(tenant_id)


def reset_tenant(token) -> None:
    _current_tenant.reset(token)


def store() -> Store:
    return memory_store()


def reset_store() -> None:
    reset_memory_store()
    reset_packets()
    reset_sms()
    reset_recap()


def _save_lead(lead: Lead) -> None:
    if using_database():
        persist_lead(lead)
        return
    store().leads.append(lead)


def _save_note(note: Note) -> None:
    if using_database():
        persist_note(note)
        return
    store().notes.append(note)


def _reject_tenant_argument(arguments: dict[str, Any]) -> None:
    # Tenant comes from the token. If the model passes tenant_id, ignore and refuse to trust it.
    if "tenant_id" in arguments:
        arguments = {key: value for key, value in arguments.items() if key != "tenant_id"}
    return None


def lookup_customer(*, phone: str, **ignored: Any) -> dict[str, Any]:
    _reject_tenant_argument(ignored)
    tenant_id = current_tenant()
    for customer in store().customers:
        if customer.get("tenant_id") == tenant_id and customer.get("phone") == phone:
            return {"found": True, "name": customer.get("name"), "address": customer.get("address")}
    return {"found": False}


def get_service_area(*, zip_code: str, **ignored: Any) -> dict[str, Any]:
    _reject_tenant_argument(ignored)
    tenant_id = current_tenant()
    try:
        zip5 = normalize_zip(zip_code)
    except PacketError:
        return {"zip": zip_code, "in_area": False}
    packet = packet_for(tenant_id)
    zips = packet.service_area_zips if packet is not None else ()
    return {"zip": zip5, "in_area": zip5 in zips}


def check_availability(**ignored: Any) -> dict[str, Any]:
    _reject_tenant_argument(ignored)
    current_tenant()
    # Real calendar booking ships when three paying customers ask. Empty is honest.
    return {"slots": [], "note": "Mabel does not invent an arrival time."}


def get_job_history(*, phone: str, **ignored: Any) -> dict[str, Any]:
    _reject_tenant_argument(ignored)
    tenant_id = current_tenant()
    jobs = [
        job
        for job in store().jobs
        if job.get("tenant_id") == tenant_id and job.get("phone") == phone
    ]
    return {"jobs": jobs}


def create_lead(
    *,
    name: str,
    address: str,
    callback: str,
    problem: str,
    urgency: str,
    source: str,
    **ignored: Any,
) -> dict[str, Any]:
    _reject_tenant_argument(ignored)
    tenant_id = current_tenant()
    lead = Lead(
        id=uuid4(),
        tenant_id=tenant_id,
        name=name,
        address=address,
        callback=callback,
        problem=problem,
        urgency=urgency,
        source=source,
    )
    _save_lead(lead)
    return {"lead_id": str(lead.id), "created": True}


def escalate_emergency(
    *,
    vertical: str,
    utterances: list[str],
    captured: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    **ignored: Any,
) -> dict[str, Any]:
    _reject_tenant_argument(ignored)
    tenant_id = current_tenant()
    rules = load_latest_rules(vertical)
    result = evaluate_scenario(
        rules,
        {
            "utterances": utterances,
            "captured": captured or {},
            "context": context or {},
        },
    )
    notify = result["notify"]
    if not result["escalate"] or not result["trigger"] or notify != "now":
        recap = queue_morning_recap(tenant_id)
        return {
            "escalated": False,
            "notify": "recap_7am",
            "reason": "No matching emergency rule.",
            "capture_gaps": result["capture_gaps"],
            "sms": {"sent": False, "reason": REASON_RECAP},
            "recap": {"queued": True, "recap_at": recap.recap_at.isoformat()},
        }
    lead = Lead(
        id=uuid4(),
        tenant_id=tenant_id,
        name=str((captured or {}).get("name") or ""),
        address=str((captured or {}).get("address") or ""),
        callback=str((captured or {}).get("callback") or ""),
        problem=str((captured or {}).get("problem") or ""),
        urgency=str((captured or {}).get("urgency") or ""),
        source=str((captured or {}).get("source") or ""),
        emergency_code=result["trigger"],
    )
    # Lead first. SMS failure must not lose it.
    _save_lead(lead)
    sms = send_owner_emergency_sms(
        tenant_id=tenant_id,
        trigger=str(result["trigger"]),
        address=lead.address,
        problem=lead.problem,
        callback=lead.callback,
    )
    return {
        "escalated": True,
        "notify": "now",
        "trigger": result["trigger"],
        "lead_id": str(lead.id),
        "capture_gaps": result["capture_gaps"],
        "sms": sms,
    }


def book_estimate(**ignored: Any) -> dict[str, Any]:
    _reject_tenant_argument(ignored)
    current_tenant()
    return {
        "booked": False,
        "reason": "Mabel does not promise an arrival time unless it came back from the calendar.",
    }


def log_note(*, body: str, **ignored: Any) -> dict[str, Any]:
    _reject_tenant_argument(ignored)
    tenant_id = current_tenant()
    note = Note(id=uuid4(), tenant_id=tenant_id, body=body)
    _save_note(note)
    return {"note_id": str(note.id), "written": True}


HANDLERS = {
    "lookup_customer": lookup_customer,
    "get_service_area": get_service_area,
    "check_availability": check_availability,
    "get_job_history": get_job_history,
    "create_lead": create_lead,
    "escalate_emergency": escalate_emergency,
    "book_estimate": book_estimate,
    "log_note": log_note,
}


TOOL_SCHEMAS = [
    {
        "name": "lookup_customer",
        "description": "Look up a returning caller by phone for this shop only.",
        "inputSchema": {
            "type": "object",
            "properties": {"phone": {"type": "string"}},
            "required": ["phone"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_service_area",
        "description": "Check whether a zip is in this shop's area.",
        "inputSchema": {
            "type": "object",
            "properties": {"zip_code": {"type": "string"}},
            "required": ["zip_code"],
            "additionalProperties": False,
        },
    },
    {
        "name": "check_availability",
        "description": "Return calendar slots. Empty until real booking ships.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "create_lead",
        "description": "Save a lead for this shop. Tenant comes from the token, not arguments.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "address": {"type": "string"},
                "callback": {"type": "string"},
                "problem": {"type": "string"},
                "urgency": {"type": "string"},
                "source": {"type": "string"},
            },
            "required": ["name", "address", "callback", "problem", "urgency", "source"],
            "additionalProperties": False,
        },
    },
    {
        "name": "escalate_emergency",
        "description": "Text the owner now only if a vertical rule matches. No match, no escalate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "vertical": {"type": "string"},
                "utterances": {"type": "array", "items": {"type": "string"}},
                "captured": {"type": "object"},
                "context": {"type": "object"},
            },
            "required": ["vertical", "utterances"],
            "additionalProperties": False,
        },
    },
    {
        "name": "book_estimate",
        "description": "Book an on-site estimate only from the calendar. Will not invent a time.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_job_history",
        "description": "Jobs this shop already has for a phone number.",
        "inputSchema": {
            "type": "object",
            "properties": {"phone": {"type": "string"}},
            "required": ["phone"],
            "additionalProperties": False,
        },
    },
    {
        "name": "log_note",
        "description": "Write a note on this shop's thread.",
        "inputSchema": {
            "type": "object",
            "properties": {"body": {"type": "string"}},
            "required": ["body"],
            "additionalProperties": False,
        },
    },
]


def call_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    if name not in HANDLERS:
        raise ToolError(f"Mabel does not have a tool named {name}.")
    args = dict(arguments or {})
    args.pop("tenant_id", None)
    return HANDLERS[name](**args)
