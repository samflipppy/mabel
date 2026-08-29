"""The nine tools, and the one rule that matters about their shape.

03-VOICE.md: 'No handler accepts a tenant identifier as an argument.' These
tests make that structural, so a tenth tool cannot quietly introduce one.
"""

from __future__ import annotations

import pytest

from mabel_mcp.schemas import (
    BY_NAME,
    TOOL_NAMES,
    TOOLS,
    SchemaViolation,
    ToolSchema,
    assert_no_tenant_argument,
)
from mabel_xai.client import ALLOWED_TOOLS


class TestTheList:
    def test_it_is_the_nine_from_03_voice(self):
        assert TOOL_NAMES == (
            "lookup_customer",
            "get_service_area",
            "check_availability",
            "create_lead",
            "escalate_emergency",
            "book_estimate",
            "get_job_history",
            "answer_question",
            "log_note",
        )

    def test_it_matches_what_the_session_config_allows(self):
        """Three places name this list: here, the session config, and the agent
        template. A tool defined but not allowed is dead code; a tool allowed
        but not defined is a call that fails mid-conversation."""
        assert TOOL_NAMES == ALLOWED_TOOLS

    @pytest.mark.parametrize("banned", ["web_search", "x_search", "file_search"])
    def test_the_forbidden_ones_are_absent(self, banned):
        assert banned not in TOOL_NAMES

    def test_names_are_unique(self):
        assert len(set(TOOL_NAMES)) == len(TOOL_NAMES)

    def test_the_lookup_is_complete(self):
        assert set(BY_NAME) == set(TOOL_NAMES)


class TestNoToolNamesATenant:
    @pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
    def test_no_tenant_argument(self, tool: ToolSchema):
        assert_no_tenant_argument(tool)

    @pytest.mark.parametrize(
        "sneaky", ["tenant_id", "tenant", "account_id", "shop_id", "org_id", "business_id"]
    )
    def test_the_guard_actually_catches_one(self, sneaky):
        """A guard nobody has seen fail is a guard nobody knows works."""
        bad = ToolSchema(
            name="reach_across",
            description="x",
            input_schema={"type": "object", "properties": {sneaky: {"type": "string"}}},
        )
        with pytest.raises(SchemaViolation, match="whose data"):
            assert_no_tenant_argument(bad)

    def test_the_guard_is_case_and_separator_insensitive(self):
        bad = ToolSchema(
            name="reach_across",
            description="x",
            input_schema={"type": "object", "properties": {"Tenant-Id": {"type": "string"}}},
        )
        with pytest.raises(SchemaViolation):
            assert_no_tenant_argument(bad)

    def test_an_ordinary_tool_passes(self):
        fine = ToolSchema(
            name="fine",
            description="x",
            input_schema={"type": "object", "properties": {"phone": {"type": "string"}}},
        )
        assert_no_tenant_argument(fine)


class TestSchemaShape:
    @pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
    def test_every_schema_is_an_object(self, tool: ToolSchema):
        assert tool.input_schema["type"] == "object"
        assert "properties" in tool.input_schema

    @pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
    def test_required_fields_are_declared_properties(self, tool: ToolSchema):
        properties = set(tool.input_schema["properties"])
        for name in tool.input_schema.get("required", []):
            assert name in properties, f"{tool.name} requires {name} but never declares it"

    @pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
    def test_every_tool_has_a_description(self, tool: ToolSchema):
        # The description is the prompt. A tool with a thin one gets called at
        # the wrong moment.
        assert len(tool.description.split()) >= 6, f"{tool.name} needs a fuller description"

    @pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
    def test_the_mcp_form_is_well_shaped(self, tool: ToolSchema):
        payload = tool.as_mcp()
        assert set(payload) == {"name", "description", "inputSchema"}
        assert payload["name"] == tool.name

    def test_lookup_customer_needs_a_phone_or_an_address(self):
        schema = BY_NAME["lookup_customer"].input_schema
        assert schema["anyOf"] == [{"required": ["phone"]}, {"required": ["address"]}]

    def test_create_lead_requires_the_four_that_make_a_lead_useful(self):
        assert set(BY_NAME["create_lead"].input_schema["required"]) == {
            "name",
            "phone",
            "job_type",
            "urgency",
        }

    def test_urgency_is_a_closed_set_matching_the_leads_table(self):
        urgency = BY_NAME["create_lead"].input_schema["properties"]["urgency"]
        assert urgency["enum"] == ["routine", "soon", "emergency"]

    def test_escalate_emergency_asks_whether_the_caller_is_safe(self):
        # Not required, because a caller in trouble should not be held on the
        # line for a form field. But asked, because the answer changes what the
        # owner does when he picks up.
        assert "caller_is_safe" in BY_NAME["escalate_emergency"].input_schema["properties"]


class TestNoToolTakesMoney:
    """Invariant 4 at the tool boundary. If a tool accepted an amount, an LLM
    output would become a dollar figure the moment she called it."""

    @pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
    def test_no_money_shaped_argument(self, tool: ToolSchema):
        forbidden = {
            "price",
            "amount",
            "cost",
            "value",
            "value_cents",
            "quote",
            "estimate",
            "total",
            "rate",
            "deposit",
        }
        offenders = forbidden & {k.lower() for k in tool.input_schema["properties"]}
        assert not offenders, (
            f"{tool.name} accepts {sorted(offenders)}. Mabel may discuss a job. "
            "She may never quote one, and a tool that takes an amount is how "
            "she starts."
        )

    @pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
    def test_no_description_invites_a_quote(self, tool: ToolSchema):
        lowered = tool.description.lower()
        for word in ("price", "quote", "cost", "estimate cost", "how much"):
            if word == "quote":
                # book_estimate and check_availability legitimately say
                # "never quote"; what matters is that none of them invite it.
                assert "never quote" in lowered or "quote" not in lowered
                continue
            assert word not in lowered, f"{tool.name} description mentions {word!r}"


class TestMutatingTools:
    def test_the_four_that_change_something_are_marked(self):
        mutating = {t.name for t in TOOLS if t.mutating}
        assert mutating == {"create_lead", "escalate_emergency", "book_estimate", "log_note"}

    def test_the_read_only_ones_are_not(self):
        # The tool trace in the portal shows the owner what she actually did.
        # Mislabelling a read as a write, or the reverse, makes that trace lie.
        assert not BY_NAME["lookup_customer"].mutating
        assert not BY_NAME["answer_question"].mutating
        assert not BY_NAME["check_availability"].mutating


class TestArrivalTimes:
    def test_check_availability_is_the_only_source_of_a_time(self):
        """'Never promise an arrival time not returned by check_availability.'
        book_estimate takes a slot_id rather than a free-text time, so she
        cannot book something the availability tool never offered."""
        book = BY_NAME["book_estimate"].input_schema["properties"]
        assert "slot_id" in book
        assert "time" not in book
        assert "starts_at" not in book
        assert "arrival_time" not in book
