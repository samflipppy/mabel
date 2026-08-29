"""The nine tools, exactly as 03-VOICE.md defines them.

This module is the contract. It holds no logic — the handlers live in
`tools/`. Keeping the schemas separate means the list Mabel is allowed to call
can be read, diffed, and asserted on without loading a database driver.

**The list is exhaustive.** Not `web_search` ($5/1k, and she starts answering
from the open internet), not `x_search`, not `file_search`. Adding a tenth tool
means changing `allowed_tools` in the session config, the agent template, and
this file, and a test fails if those three disagree.

**No tool takes a tenant identifier.** Not as a required argument, not as an
optional one. The tenant comes from the JWT the DID resolution minted.
`assert_no_tenant_argument` enforces that against every schema here, so a new
tool cannot quietly introduce one.

On the count: 03-VOICE.md lists nine, AGENTS.md says eight. The difference is
`answer_question`, and 03-VOICE.md defines it in full including its
`{found: false}` contract — the tool that stops her guessing when she has no
answer. Resolved in favour of nine, recorded in docs/xai_notes.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Anything that looks like it names a tenant. A tool argument carrying one of
# these would let the model choose whose data it reads, which is the single
# thing this design exists to prevent.
FORBIDDEN_ARGUMENT_NAMES = frozenset(
    {
        "tenant",
        "tenant_id",
        "tenantid",
        "account",
        "account_id",
        "business_id",
        "shop_id",
        "org",
        "org_id",
        "customer_id",
        "location_id",
    }
)


@dataclass(frozen=True, slots=True)
class ToolSchema:
    name: str
    description: str
    input_schema: dict[str, Any]
    # Tools that change something. The QA pass and the tool trace in the portal
    # both care about the difference between looking and doing.
    mutating: bool = False

    def as_mcp(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


LOOKUP_CUSTOMER = ToolSchema(
    name="lookup_customer",
    description="Check whether this caller is an existing customer.",
    input_schema={
        "type": "object",
        "properties": {"phone": {"type": "string"}, "address": {"type": "string"}},
        "anyOf": [{"required": ["phone"]}, {"required": ["address"]}],
    },
)

GET_SERVICE_AREA = ToolSchema(
    name="get_service_area",
    description=(
        "Check whether an address is in the service area. If it is not, say so "
        "politely and offer to take a message anyway."
    ),
    input_schema={
        "type": "object",
        "properties": {"zip": {"type": "string"}, "city": {"type": "string"}},
        "required": ["zip"],
    },
)

CHECK_AVAILABILITY = ToolSchema(
    name="check_availability",
    description=(
        "Get real appointment windows. Never state an arrival time that did not "
        "come back from this tool."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "job_type": {"type": "string"},
            "preferred_window": {"type": "string"},
        },
        "required": ["job_type"],
    },
)

CREATE_LEAD = ToolSchema(
    name="create_lead",
    description="Record the job. Call this once you have the caller's details.",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "phone": {"type": "string"},
            "address": {"type": "string"},
            "job_type": {"type": "string"},
            "description": {"type": "string"},
            "urgency": {"enum": ["routine", "soon", "emergency"]},
            "source": {"type": "string"},
        },
        "required": ["name", "phone", "job_type", "urgency"],
    },
    mutating=True,
)

ESCALATE_EMERGENCY = ToolSchema(
    name="escalate_emergency",
    description=(
        "Wake somebody up. Texts whoever is on call immediately and also creates "
        "the lead. Use only for a genuine emergency."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "phone": {"type": "string"},
            "address": {"type": "string"},
            "nature": {"type": "string"},
            "caller_is_safe": {"type": "boolean"},
        },
        "required": ["name", "phone", "nature"],
    },
    mutating=True,
)

BOOK_ESTIMATE = ToolSchema(
    name="book_estimate",
    description=(
        "Book one of the windows check_availability returned. Never book a time "
        "that tool did not offer."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "slot_id": {"type": "string"},
            "name": {"type": "string"},
            "phone": {"type": "string"},
            "address": {"type": "string"},
            "job_type": {"type": "string"},
        },
        "required": ["slot_id", "name", "phone"],
    },
    mutating=True,
)

GET_JOB_HISTORY = ToolSchema(
    name="get_job_history",
    description="Past jobs for a caller we already know, so she can pick up the thread.",
    input_schema={
        "type": "object",
        "properties": {"phone": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["phone"],
    },
)

ANSWER_QUESTION = ToolSchema(
    name="answer_question",
    description=(
        "Look up an answer in this business's own Q&A. If it returns found: false, "
        "say someone will follow up. Do not guess."
    ),
    input_schema={
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    },
)

LOG_NOTE = ToolSchema(
    name="log_note",
    description="Record something the caller said that does not fit the other fields.",
    input_schema={
        "type": "object",
        "properties": {"note": {"type": "string"}},
        "required": ["note"],
    },
    mutating=True,
)

# Order matches 03-VOICE.md's allowed_tools.
TOOLS: tuple[ToolSchema, ...] = (
    LOOKUP_CUSTOMER,
    GET_SERVICE_AREA,
    CHECK_AVAILABILITY,
    CREATE_LEAD,
    ESCALATE_EMERGENCY,
    BOOK_ESTIMATE,
    GET_JOB_HISTORY,
    ANSWER_QUESTION,
    LOG_NOTE,
)

TOOL_NAMES: tuple[str, ...] = tuple(t.name for t in TOOLS)
BY_NAME: dict[str, ToolSchema] = {t.name: t for t in TOOLS}


class SchemaViolation(ValueError):
    """A tool schema that would let the model choose whose data it sees."""


def assert_no_tenant_argument(schema: ToolSchema) -> None:
    """No tool accepts a tenant identifier. Structural, not a convention.

    03-VOICE.md: 'Every handler resolves tenant from the JWT, sets SET LOCAL
    app.tenant_id, and filters on it. No handler accepts a tenant identifier as
    an argument.' This is that sentence, enforced.
    """
    properties = schema.input_schema.get("properties", {})
    offenders = sorted(
        name for name in properties if name.lower().replace("-", "_") in FORBIDDEN_ARGUMENT_NAMES
    )
    if offenders:
        raise SchemaViolation(
            f"{schema.name} accepts {offenders}, which would let the model choose "
            "whose data it reads. The tenant comes from the call token, which was "
            "minted from the dialed number before the session opened."
        )


def validate_all() -> None:
    """Run at import so a bad schema fails the process, not a call."""
    for schema in TOOLS:
        assert_no_tenant_argument(schema)


validate_all()
