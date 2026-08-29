"""The nine handlers.

No database. What is under test here is the decisions: what she is allowed to
know, what she is allowed to say, and what happens when the model passes
something wrong. The SQL underneath is exercised in `tests/isolation/`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from mabel_mcp.registry import HANDLERS, ToolNotFound, ToolTrace, dispatch_with_repo, list_tools
from mabel_mcp.repo import FakeRepo, ToolContext
from mabel_mcp.schemas import TOOL_NAMES
from mabel_mcp.tools.area import get_service_area
from mabel_mcp.tools.capture import (
    EMERGENCY_SMS_LIMIT,
    compose_emergency_sms,
    create_lead,
    escalate_emergency,
    log_note,
)
from mabel_mcp.tools.knowledge import answer_question
from mabel_mcp.tools.lookup import get_job_history, lookup_customer
from mabel_mcp.tools.scheduling import book_estimate, check_availability

NOW = datetime(2026, 10, 14, 2, 30, tzinfo=UTC)


def ctx(repo: FakeRepo) -> ToolContext:
    return ToolContext(repo=repo, call_id="call_abc", now=NOW)


class FakeConfig:
    def __init__(self, zips=(), note=None):
        self.service_area_zips = list(zips)
        self.service_area_note = note


class TestLookupCustomer:
    async def test_a_known_caller_is_recognised(self):
        repo = FakeRepo(
            contact={"id": uuid4(), "display_name": "Mrs. Henderson"},
            last_job_row={
                "job_type": "exterior painting",
                "when": datetime(2026, 4, 18, tzinfo=UTC),
            },
        )
        result = await lookup_customer(ctx(repo), {"phone": "216-555-0148"})
        assert result["found"] is True
        assert result["name"] == "Mrs. Henderson"
        assert result["last_job"] == "exterior painting"
        assert result["last_job_date"] == "2026-04-18"

    async def test_the_number_is_normalised_before_the_lookup(self):
        # Two spellings of one number must not be two customers.
        repo = FakeRepo(contact=None)
        await lookup_customer(ctx(repo), {"phone": "(216) 555-0148"})
        assert repo.args_for("find_contact_by_phone")["phone_e164"] == "+12165550148"

    async def test_an_unknown_caller_is_not_found(self):
        result = await lookup_customer(ctx(FakeRepo(contact=None)), {"phone": "2165550148"})
        assert result["found"] is False
        assert result["name"] is None

    async def test_an_unusable_number_does_not_raise(self):
        result = await lookup_customer(ctx(FakeRepo()), {"phone": "not a phone"})
        assert result["found"] is False

    async def test_no_arguments_at_all_is_answered_not_raised(self):
        result = await lookup_customer(ctx(FakeRepo()), {})
        assert result["found"] is False

    async def test_address_only_says_so_rather_than_guessing(self):
        """Greeting the wrong person by name is worse than not greeting them."""
        repo = FakeRepo()
        result = await lookup_customer(ctx(repo), {"address": "100 Example Ave"})
        assert result["found"] is False
        assert not repo.called("find_contact_by_phone")

    async def test_open_balance_is_present_and_null(self):
        """03-VOICE.md puts it in the contract. 01-SCHEMA.sql has no invoicing,
        payments, or balance to compute it from -- see BLOCKED.md S5. The key
        stays so the contract does not change when a source exists."""
        repo = FakeRepo(contact={"id": uuid4(), "display_name": "X"}, last_job_row=None)
        result = await lookup_customer(ctx(repo), {"phone": "2165550148"})
        assert "open_balance" in result
        assert result["open_balance"] is None


class TestJobHistoryNeverCarriesAValue:
    async def test_no_money_reaches_the_model(self):
        """The repo already drops value_cents; this proves the handler does not
        put it back. A job value read aloud is a quote in the caller's ears."""
        repo = FakeRepo(
            contact={"id": uuid4(), "display_name": "X"},
            history=[
                {
                    "job_type": "water heater",
                    "status": "won",
                    "created_at": datetime(2026, 3, 1, tzinfo=UTC),
                    "value_cents": 380_000,
                }
            ],
        )
        result = await get_job_history(ctx(repo), {"phone": "2165550148"})
        blob = json.dumps(result)
        assert "380000" not in blob
        assert "value" not in blob
        assert result["jobs"][0]["job_type"] == "water heater"

    async def test_the_limit_is_bounded(self):
        repo = FakeRepo(contact={"id": uuid4()}, history=[])
        await get_job_history(ctx(repo), {"phone": "2165550148", "limit": 5000})
        assert repo.args_for("job_history")["limit"] == 10

    async def test_a_nonsense_limit_falls_back_to_the_default(self):
        repo = FakeRepo(contact={"id": uuid4()}, history=[])
        await get_job_history(ctx(repo), {"phone": "2165550148", "limit": "lots"})
        assert repo.args_for("job_history")["limit"] == 3

    async def test_an_unknown_caller_gets_an_empty_history(self):
        result = await get_job_history(ctx(FakeRepo(contact=None)), {"phone": "2165550148"})
        assert result == {"found": False, "jobs": []}


class TestServiceArea:
    async def test_a_zip_in_the_list_is_in_area(self):
        repo = FakeRepo(config=FakeConfig(zips=["44107", "44116"]))
        assert (await get_service_area(ctx(repo), {"zip": "44107"}))["in_area"] is True

    async def test_a_zip_outside_the_list_is_not(self):
        repo = FakeRepo(config=FakeConfig(zips=["44107"]))
        result = await get_service_area(ctx(repo), {"zip": "44060"})
        assert result["in_area"] is False
        assert "message" in result["note"].lower()

    async def test_the_tenants_own_wording_is_used_when_set(self):
        note = "We don't get out that far, but leave a number and Ray will call."
        repo = FakeRepo(config=FakeConfig(zips=["44107"], note=note))
        assert (await get_service_area(ctx(repo), {"zip": "44060"}))["note"] == note

    async def test_a_zip_plus_four_still_matches(self):
        repo = FakeRepo(config=FakeConfig(zips=["44107"]))
        assert (await get_service_area(ctx(repo), {"zip": "44107-1234"}))["in_area"] is True

    async def test_an_empty_zip_list_means_everywhere(self):
        """Refusing every caller because a config screen is blank is the worse
        failure by a distance."""
        repo = FakeRepo(config=FakeConfig(zips=[]))
        assert (await get_service_area(ctx(repo), {"zip": "44060"}))["in_area"] is True

    async def test_no_published_config_does_not_promise_coverage(self):
        result = await get_service_area(ctx(FakeRepo(config=None)), {"zip": "44107"})
        assert result["in_area"] is False

    async def test_a_non_zip_is_refused_politely(self):
        repo = FakeRepo(config=FakeConfig(zips=["44107"]))
        result = await get_service_area(ctx(repo), {"zip": "Lakewood"})
        assert result["in_area"] is False


class TestSheNeverInventsATime:
    SLOTS = [
        {
            "slot_id": "slot_aaa",
            "day": "2026-10-15",
            "label": "morning",
            "spoken": "Thursday morning",
        },
        {
            "slot_id": "slot_bbb",
            "day": "2026-10-15",
            "label": "afternoon",
            "spoken": "Thursday afternoon",
        },
    ]

    async def test_only_real_windows_come_back(self):
        repo = FakeRepo(slots=self.SLOTS)
        result = await check_availability(ctx(repo), {"job_type": "water heater"})
        assert [s["slot_id"] for s in result["slots"]] == ["slot_aaa", "slot_bbb"]

    async def test_no_windows_is_an_honest_empty_list(self):
        """The prompt tells her what to do with this: take the details and say
        someone will call to arrange a time. Inventing one is the failure."""
        result = await check_availability(ctx(FakeRepo(slots=[])), {"job_type": "x"})
        assert result["slots"] == []

    async def test_the_spoken_form_is_coarse(self):
        # "Thursday morning" is a promise a contractor can keep. "Thursday at
        # 9:15" is one he cannot.
        repo = FakeRepo(slots=self.SLOTS)
        result = await check_availability(ctx(repo), {"job_type": "x"})
        assert result["slots"][0]["spoken"] == "Thursday morning"

    async def test_a_preference_reorders_but_never_adds(self):
        repo = FakeRepo(slots=self.SLOTS)
        result = await check_availability(
            ctx(repo), {"job_type": "x", "preferred_window": "afternoon"}
        )
        assert result["slots"][0]["spoken"] == "Thursday afternoon"
        assert len(result["slots"]) == 2

    async def test_booking_an_invented_slot_fails(self):
        """The rule enforced by shape. There is no free-text time argument, and
        an id she made up is not in the list."""
        repo = FakeRepo(booked=False)
        result = await book_estimate(
            ctx(repo), {"slot_id": "slot_i_made_this_up", "name": "Pat", "phone": "2165550148"}
        )
        assert result["booked"] is False

    async def test_booking_a_real_slot_works(self):
        repo = FakeRepo(booked=True)
        result = await book_estimate(
            ctx(repo), {"slot_id": "slot_aaa", "name": "Pat", "phone": "2165550148"}
        )
        assert result["booked"] is True
        assert repo.called("record_event")

    async def test_booking_with_an_unusable_number_fails_before_writing(self):
        repo = FakeRepo()
        result = await book_estimate(ctx(repo), {"slot_id": "slot_aaa", "phone": "nope"})
        assert result["booked"] is False
        assert not repo.called("book_slot")


class TestCreateLead:
    async def test_the_ordinary_case(self):
        repo = FakeRepo()
        result = await create_lead(
            ctx(repo),
            {
                "name": "Pat Example",
                "phone": "216-555-0148",
                "address": "100 Example Ave",
                "job_type": "burst pipe",
                "urgency": "emergency",
                "source": "google",
            },
        )
        assert result["created"] is True
        written = repo.args_for("create_lead")
        assert written["callback_e164"] == "+12165550148"
        assert written["urgency"] == "emergency"

    async def test_no_money_field_is_ever_written(self):
        """leads.value_cents is owner-entered. Nothing on the call path writes
        it, and everything this handler has came from a language model."""
        repo = FakeRepo()
        await create_lead(
            ctx(repo),
            {"name": "Pat", "phone": "2165550148", "job_type": "x", "urgency": "routine"},
        )
        written = repo.args_for("create_lead")
        assert "value_cents" not in written
        assert not any("value" in key or "price" in key for key in written)

    async def test_an_unusable_number_is_refused_before_writing(self):
        repo = FakeRepo()
        result = await create_lead(
            ctx(repo),
            {"name": "Pat", "phone": "call me back", "job_type": "x", "urgency": "routine"},
        )
        assert result["created"] is False
        assert not repo.called("create_lead")

    async def test_an_urgency_outside_the_enum_defaults_down_not_up(self):
        """Over-calling an emergency on a parse error wakes somebody for
        nothing, and doing that on a typo rather than on evidence is how the
        owner stops trusting the alerts."""
        repo = FakeRepo()
        result = await create_lead(
            ctx(repo),
            {"name": "Pat", "phone": "2165550148", "job_type": "x", "urgency": "VERY URGENT!!"},
        )
        assert result["urgency"] == "routine"

    async def test_the_thread_gets_a_row(self):
        repo = FakeRepo()
        await create_lead(
            ctx(repo),
            {"name": "Pat", "phone": "2165550148", "job_type": "x", "urgency": "routine"},
        )
        assert repo.called("record_event")


class TestEscalateEmergency:
    async def test_it_creates_the_lead_as_well_as_texting(self):
        """An emergency that produced a phone call but no record is one the
        office manager cannot find in the morning."""
        repo = FakeRepo()
        result = await escalate_emergency(
            ctx(repo),
            {"name": "Pat", "phone": "2165550148", "nature": "burst pipe", "caller_is_safe": True},
        )
        assert result["escalated"] is True
        assert repo.called("create_lead")
        assert repo.called("notify_oncall")
        assert repo.args_for("create_lead")["urgency"] == "emergency"

    async def test_the_lead_is_marked_escalated(self):
        repo = FakeRepo()
        await escalate_emergency(
            ctx(repo), {"name": "Pat", "phone": "2165550148", "nature": "flooding"}
        )
        assert repo.args_for("create_lead")["escalated_at"] == NOW

    async def test_nobody_on_call_is_reported_honestly(self):
        """She must not imply a truck is moving when nobody was reached."""
        repo = FakeRepo(notified=False)
        result = await escalate_emergency(
            ctx(repo), {"name": "Pat", "phone": "2165550148", "nature": "flooding"}
        )
        assert result["escalated"] is True
        assert result["oncall_reached"] is False

    async def test_an_unusable_number_is_refused(self):
        repo = FakeRepo()
        result = await escalate_emergency(ctx(repo), {"name": "Pat", "phone": "x", "nature": "y"})
        assert result["escalated"] is False
        assert not repo.called("notify_oncall")


class TestTheEmergencySms:
    """What an owner reads at 3am, one thumb, half asleep."""

    def test_it_leads_with_the_word_that_matters(self):
        body = compose_emergency_sms(
            name="Pat Example",
            phone="+12165550148",
            address="100 Example Ave",
            nature="burst pipe in the basement",
            caller_is_safe=True,
        )
        assert body.startswith("EMERGENCY")

    def test_it_carries_a_dialable_number(self):
        body = compose_emergency_sms(
            name="Pat", phone="+12165550148", address=None, nature="flooding", caller_is_safe=None
        )
        assert "(216) 555-0148" in body

    def test_it_fits_one_segment(self):
        body = compose_emergency_sms(
            name="Bartholomew Fitzwilliam-Harrington",
            phone="+12165550148",
            address="1234 Extraordinarily Long Boulevard Northwest, Lakewood, Ohio 44107",
            nature="water is coming through the ceiling and the basement is filling up fast",
            caller_is_safe=False,
        )
        assert len(body) <= EMERGENCY_SMS_LIMIT

    def test_the_number_survives_truncation(self):
        """He can act on a number with no description. He can do nothing with a
        description and no number."""
        body = compose_emergency_sms(
            name="X" * 200,
            phone="+12165550148",
            address="Y" * 200,
            nature="Z" * 200,
            caller_is_safe=None,
        )
        assert body.endswith("(216) 555-0148")
        assert len(body) <= EMERGENCY_SMS_LIMIT

    def test_unsafe_is_only_flagged_when_explicitly_false(self):
        # Unknown is not the same as unsafe. Crying wolf devalues the words.
        unknown = compose_emergency_sms(
            name="Pat", phone="+12165550148", address=None, nature="x", caller_is_safe=None
        )
        unsafe = compose_emergency_sms(
            name="Pat", phone="+12165550148", address=None, nature="x", caller_is_safe=False
        )
        assert "NOT SAFE" not in unknown
        assert "NOT SAFE" in unsafe

    def test_no_dollar_figure_can_appear(self):
        body = compose_emergency_sms(
            name="Pat", phone="+12165550148", address=None, nature="burst pipe", caller_is_safe=True
        )
        assert "$" not in body


class TestAnswerQuestion:
    async def test_a_match_returns_the_owners_own_words(self):
        repo = FakeRepo(
            knowledge=[
                {
                    "question": "Do you do drywall repair?",
                    "answer": "Yes, as part of a painting job.",
                }
            ]
        )
        result = await answer_question(ctx(repo), {"question": "do you fix drywall"})
        assert result["found"] is True
        assert result["answer"] == "Yes, as part of a painting job."

    async def test_no_match_says_so(self):
        """The whole point of the tool. A model asked 'do you do drywall?' will
        answer whether or not it has any basis to."""
        result = await answer_question(ctx(FakeRepo(knowledge=[])), {"question": "do you do roofs"})
        assert result["found"] is False
        assert result["answer"] is None
        assert "guess" in result["guidance"].lower()

    async def test_an_empty_question_is_not_found(self):
        assert (await answer_question(ctx(FakeRepo()), {"question": "  "}))["found"] is False

    async def test_a_blank_stored_answer_is_treated_as_no_answer(self):
        repo = FakeRepo(knowledge=[{"question": "x", "answer": "   "}])
        assert (await answer_question(ctx(repo), {"question": "x"}))["found"] is False

    async def test_only_the_best_match_is_returned_never_a_blend(self):
        # Combining two entries is answering on the business's behalf.
        repo = FakeRepo(
            knowledge=[
                {"question": "a", "answer": "First answer."},
                {"question": "b", "answer": "Second answer."},
            ]
        )
        result = await answer_question(ctx(repo), {"question": "a"})
        assert result["answer"] == "First answer."
        assert "Second" not in result["answer"]


class TestLogNote:
    async def test_a_note_lands_in_the_thread(self):
        repo = FakeRepo()
        result = await log_note(ctx(repo), {"note": "Gate code is 4417."})
        assert result["logged"] is True
        assert repo.args_for("record_event")["kind"] == "note"

    async def test_an_empty_note_is_not_written(self):
        repo = FakeRepo()
        assert (await log_note(ctx(repo), {"note": "   "}))["logged"] is False
        assert not repo.called("record_event")


class TestDispatch:
    async def test_every_schema_has_a_handler(self):
        assert set(HANDLERS) == set(TOOL_NAMES)

    async def test_tools_list_is_the_nine_in_order(self):
        assert [t["name"] for t in list_tools()] == list(TOOL_NAMES)

    async def test_an_unknown_tool_raises(self):
        with pytest.raises(ToolNotFound):
            await dispatch_with_repo("delete_everything", {}, repo=FakeRepo())

    async def test_a_handler_that_raises_does_not_take_the_call_down(self):
        """She is mid-conversation with somebody standing in six inches of
        water. A structured error lets her carry on; an exception is dead air."""

        class Exploding(FakeRepo):
            async def find_contact_by_phone(self, phone_e164):
                raise RuntimeError("the database fell over")

        result = await dispatch_with_repo(
            "lookup_customer", {"phone": "2165550148"}, repo=Exploding()
        )
        assert result.is_error is True
        assert "details" in result.content["message"]

    async def test_an_internal_error_message_never_reaches_the_model(self):
        """Exception text can carry row contents, and it reaches a language
        model that may read it out loud."""

        class Leaky(FakeRepo):
            async def find_contact_by_phone(self, phone_e164):
                raise RuntimeError("row: Henderson, 216-555-0148, balance 4200")

        result = await dispatch_with_repo("lookup_customer", {"phone": "2165550148"}, repo=Leaky())
        blob = json.dumps(result.content)
        assert "Henderson" not in blob
        assert "4200" not in blob

    async def test_the_mcp_form_is_json_the_model_can_read_fields_off(self):
        result = await dispatch_with_repo(
            "answer_question", {"question": "x"}, repo=FakeRepo(knowledge=[])
        )
        payload = result.as_mcp()
        assert payload["isError"] is False
        assert json.loads(payload["content"][0]["text"])["found"] is False


class TestToolTrace:
    async def test_it_records_what_she_actually_did(self):
        trace = ToolTrace()
        repo = FakeRepo()
        result = await dispatch_with_repo(
            "create_lead",
            {"name": "Pat", "phone": "2165550148", "job_type": "x", "urgency": "routine"},
            repo=repo,
        )
        trace.record(result, {"name": "Pat"})
        assert trace.called("create_lead")
        assert len(trace.mutations) == 1

    async def test_a_read_is_not_counted_as_a_mutation(self):
        trace = ToolTrace()
        result = await dispatch_with_repo(
            "answer_question", {"question": "x"}, repo=FakeRepo(knowledge=[])
        )
        trace.record(result, {"question": "x"})
        assert trace.mutations == []

    async def test_a_failed_mutation_is_not_counted(self):
        trace = ToolTrace()

        class Exploding(FakeRepo):
            async def resolve_or_create_contact(self, **_kwargs):
                raise RuntimeError("nope")

        result = await dispatch_with_repo(
            "create_lead",
            {"name": "Pat", "phone": "2165550148", "job_type": "x", "urgency": "routine"},
            repo=Exploding(),
        )
        trace.record(result, {})
        assert trace.mutations == []


class TestNoHandlerTakesATenant:
    @pytest.mark.parametrize("name", TOOL_NAMES)
    async def test_a_tenant_argument_is_ignored_not_honoured(self, name):
        """Belt to the schema's braces. Even if a tenant id reached a handler
        in `args`, nothing reads it -- the repo is already bound to one tenant,
        which is what makes the seam safe rather than the discipline."""
        repo = FakeRepo(config=FakeConfig(zips=["44107"]), knowledge=[], slots=[])
        args = {
            "tenant_id": str(uuid4()),
            "phone": "2165550148",
            "zip": "44107",
            "question": "x",
            "note": "x",
            "job_type": "x",
            "urgency": "routine",
            "name": "Pat",
            "slot_id": "slot_aaa",
        }
        result = await dispatch_with_repo(name, args, repo=repo)
        for _call, kwargs in repo.calls:
            assert "tenant_id" not in kwargs
        assert result.name == name
