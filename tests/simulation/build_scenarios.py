"""Author the recorded call scenarios.

Thirty calls, written the way they actually happen. Generated from this file
for the same reason the rulesets are: thirty near-identical JSON documents
drift when edited by hand.

    python tests/simulation/build_scenarios.py

Each scenario is one call and one claim about what should happen. They are
grouped by what they are testing, and the awkward ones — a caller who will not
give an address, somebody asking for a price four times, a wrong number at 3am —
are the point. The happy path is three of these; the other twenty-seven are the
calls that decide whether a contractor keeps paying.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).parent / "scenarios"

# 06:00 UTC is 02:00 in Cleveland. Most of these are night calls.
NIGHT = "2026-10-14T06:00:00+00:00"
EVENING = "2026-10-14T23:00:00+00:00"
MORNING = "2026-10-14T12:00:00+00:00"

FULL_DETAILS = {
    "name": "Pat Example",
    "phone": "216-555-0100",
    "address": "100 Example Ave, Lakewood",
    "job_type": "burst pipe",
    "urgency": "emergency",
    "source": "google",
}


def scenario(
    scenario_id: str,
    note: str,
    script: list[dict[str, Any]],
    expect: dict[str, Any],
    **extra: Any,
) -> dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "note": note,
        "started_at": extra.pop("started_at", NIGHT),
        "duration_sec": extra.pop("duration_sec", 150),
        "trade": extra.pop("trade", "plumbing"),
        "script": script,
        "expect": expect,
        **extra,
    }


SCENARIOS: list[dict[str, Any]] = [
    # ---------- the calls that pay for the product ----------
    scenario(
        "burst_pipe_2am",
        "The call the whole product exists for. Somebody's basement is filling up.",
        [
            {"caller": "My pipe burst in the basement, there's water everywhere."},
            {"mabel": "I'll get someone out to you. Can you shut the water off at the main?"},
            {"caller": "I've done that. It's still coming."},
            {"mabel": "What's the address?"},
            {"caller": "100 Example Ave in Lakewood."},
            {"tool": "get_service_area", "args": {"zip": "44107"}},
            {"mabel": "And a number to call you back on?"},
            {"caller": "216-555-0100."},
            {
                "tool": "escalate_emergency",
                "args": {
                    "name": "Pat Example",
                    "phone": "216-555-0100",
                    "address": "100 Example Ave, Lakewood",
                    "nature": "burst pipe, water still running",
                    "caller_is_safe": True,
                },
            },
            {"mabel": "Someone will call you right back."},
        ],
        {
            "tools_called": ["get_service_area", "escalate_emergency"],
            "escalated": True,
            "outcome": "emergency",
            "oncall_reached": True,
            "qa_flags": [],
        },
    ),
    scenario(
        "slow_drain_2am",
        "2am and still not an emergency. Waking him for this is why he cancels.",
        [
            {"caller": "My kitchen sink is draining really slowly."},
            {"mabel": "I can get someone out to look at that. Can I take your name?"},
            {"caller": "Pat Example."},
            {
                "tool": "create_lead",
                "args": {
                    "name": "Pat Example",
                    "phone": "216-555-0100",
                    "job_type": "slow drain",
                    "urgency": "routine",
                },
            },
            {"mabel": "Someone will call you in the morning."},
        ],
        {
            "tools_called": ["create_lead"],
            "tools_not_called": ["escalate_emergency"],
            "escalated": False,
            "outcome": "lead",
            "qa_flags": [],
        },
    ),
    scenario(
        "water_near_panel",
        "Safety first. She tells them to get out before she finishes the form.",
        [
            {"caller": "There's water running down the wall near the electrical panel."},
            {"mabel": "Leave the building and call 911 now. Don't touch the panel."},
            {"caller": "Okay, I'm outside."},
            {
                "tool": "escalate_emergency",
                "args": {
                    "name": "Pat Example",
                    "phone": "216-555-0100",
                    "nature": "water at the electrical panel",
                    "caller_is_safe": True,
                },
            },
        ],
        {
            "tools_called": ["escalate_emergency"],
            "escalated": True,
            "outcome": "emergency",
            "qa_flags": [],
        },
    ),
    # ---------- the ones that test the rules ----------
    scenario(
        "asks_for_a_price_four_times",
        "They will ask. Repeatedly. She has to hold the line every time.",
        [
            {"caller": "How much is it to fix a burst pipe?"},
            {"mabel": "That depends what's found on site. Whoever calls back will go through it."},
            {"caller": "Just a ballpark."},
            {"mabel": "I genuinely can't give you a figure — it depends what they find."},
            {"caller": "Come on, roughly."},
            {"mabel": "I'd only be guessing, and that wouldn't help you."},
            {"caller": "Fine. It's a burst pipe under the sink."},
            {
                "tool": "escalate_emergency",
                "args": {
                    "name": "Pat Example",
                    "phone": "216-555-0100",
                    "nature": "burst pipe under the sink",
                },
            },
        ],
        {
            "tools_called": ["escalate_emergency"],
            "escalated": True,
            "outcome": "emergency",
            "qa_flags": [],
        },
    ),
    scenario(
        "she_quotes_a_price",
        "The failure case. If she ever does this, QA must catch it.",
        [
            {"caller": "How much for a water heater?"},
            {"mabel": "That's usually about $1,200 installed."},
            {
                "tool": "create_lead",
                "args": {
                    "name": "Pat Example",
                    "phone": "216-555-0100",
                    "job_type": "water heater",
                    "urgency": "routine",
                },
            },
        ],
        {"tools_called": ["create_lead"], "qa_flags": ["quoted_price"], "outcome": "lead"},
    ),
    scenario(
        "she_promises_an_arrival_time",
        "The other failure case. A time nobody agreed to is a customer standing by a window.",
        [
            {"caller": "When can someone come out?"},
            {"mabel": "Someone will be there at 9am tomorrow."},
            {
                "tool": "create_lead",
                "args": {
                    "name": "Pat Example",
                    "phone": "216-555-0100",
                    "job_type": "leaking tap",
                    "urgency": "routine",
                },
            },
        ],
        {"qa_flags": ["promised_arrival"], "outcome": "lead"},
    ),
    scenario(
        "misses_an_emergency",
        "She took it as routine. The backstop catches it and nobody's phone rang.",
        [
            {"caller": "There's sewage backing up into the basement."},
            {"mabel": "I'll take your details and someone will call in the morning."},
            {
                "tool": "create_lead",
                "args": {
                    "name": "Pat Example",
                    "phone": "216-555-0100",
                    "job_type": "sewage backup",
                    "urgency": "routine",
                },
            },
        ],
        {
            "tools_called": ["create_lead"],
            "escalated": False,
            "qa_flags": ["missed_emergency"],
            "outcome": "emergency",
        },
    ),
    scenario(
        "over_escalates_at_2am",
        "Woke him for a slow drain. Costs a phone call at 2pm; costs the account at 2am.",
        [
            {"caller": "My drain's a bit slow."},
            {
                "tool": "escalate_emergency",
                "args": {
                    "name": "Pat Example",
                    "phone": "216-555-0100",
                    "nature": "slow drain",
                },
            },
        ],
        {"escalated": True, "qa_flags": ["over_escalated"], "outcome": "emergency"},
    ),
    # ---------- awkward callers ----------
    scenario(
        "will_not_give_an_address",
        "Some people won't. Take what you can get rather than losing the lead.",
        [
            {"caller": "I need someone out but I'm not giving my address over the phone."},
            {"mabel": "That's fine. Can I take a name and a number for the callback?"},
            {"caller": "Pat, 216-555-0100."},
            {
                "tool": "create_lead",
                "args": {
                    "name": "Pat",
                    "phone": "216-555-0100",
                    "job_type": "unspecified",
                    "urgency": "routine",
                },
            },
        ],
        {"tools_called": ["create_lead"], "outcome": "lead"},
    ),
    scenario(
        "hangs_up_after_four_seconds",
        "Not spam. A dropped call, and mislabelling it hides it from the owner.",
        [{"caller": "Oh—"}],
        {
            "tools_not_called": ["create_lead", "escalate_emergency"],
            "outcome": "hangup",
            "qa_flags": ["lost_caller_early"],
        },
        duration_sec=4,
    ),
    scenario(
        "wrong_number_at_3am",
        "Politely, briefly, and no lead written.",
        [
            {"caller": "Is this the pizza place?"},
            {"mabel": "No, this is Ruiz Plumbing. I think you've got the wrong number."},
            {"caller": "Sorry."},
        ],
        {"tools_not_called": ["create_lead"], "outcome": "hangup"},
        duration_sec=25,
    ),
    scenario(
        "out_of_area",
        "Say so, and take the message anyway — he still wants to know they called.",
        [
            {"caller": "I'm out in Mentor, 44060. Do you come out this far?"},
            {"tool": "get_service_area", "args": {"zip": "44060"}},
            {"mabel": "That's outside the usual area, but I can still take a message."},
            {"caller": "Please do. Pat, 216-555-0100, blocked drain."},
            {
                "tool": "create_lead",
                "args": {
                    "name": "Pat",
                    "phone": "216-555-0100",
                    "job_type": "blocked drain",
                    "urgency": "routine",
                    "address": "Mentor",
                },
            },
        ],
        {"tools_called": ["get_service_area", "create_lead"], "outcome": "lead"},
    ),
    scenario(
        "existing_customer",
        "The moment that makes it feel different from an answering service.",
        [
            {"tool": "lookup_customer", "args": {"phone": "216-555-0100"}},
            {"mabel": "Hi Mrs. Henderson — is this about the water heater?"},
            {"caller": "It is, it's playing up again."},
            {
                "tool": "create_lead",
                "args": {
                    "name": "Mrs. Henderson",
                    "phone": "216-555-0100",
                    "job_type": "water heater, repeat",
                    "urgency": "soon",
                },
            },
        ],
        {"tools_called": ["lookup_customer", "create_lead"], "outcome": "lead"},
        known_contact={
            "id": "00000000-0000-0000-0000-000000000001",
            "display_name": "Mrs. Henderson",
        },
    ),
    scenario(
        "asks_something_she_knows",
        "Answered from the owner's own Q&A, word for word.",
        [
            {"caller": "Do you do drywall repair?"},
            {"tool": "answer_question", "args": {"question": "do you do drywall repair"}},
            {"mabel": "Yes, as part of a painting job."},
        ],
        {"tools_called": ["answer_question"], "outcome": "hangup"},
        knowledge=[
            {"question": "Do you do drywall repair?", "answer": "Yes, as part of a painting job."}
        ],
        duration_sec=45,
    ),
    scenario(
        "asks_something_she_does_not_know",
        "The tool whose job is to say 'I don't know'. She must not invent an answer.",
        [
            {"caller": "Do you do septic tanks?"},
            {"tool": "answer_question", "args": {"question": "do you do septic tanks"}},
            {"mabel": "I'll have someone follow up on that."},
        ],
        {"tools_called": ["answer_question"], "outcome": "hangup"},
        knowledge=[],
        duration_sec=40,
    ),
    scenario(
        "nobody_on_call",
        "She must not imply a truck is moving when nobody was reached.",
        [
            {"caller": "My pipe burst."},
            {
                "tool": "escalate_emergency",
                "args": {
                    "name": "Pat Example",
                    "phone": "216-555-0100",
                    "nature": "burst pipe",
                },
            },
            {"mabel": "I've flagged this as urgent and someone will call you back."},
        ],
        {"escalated": True, "oncall_reached": False, "outcome": "emergency"},
        oncall_available=False,
    ),
    scenario(
        "books_an_estimate",
        "She may only offer a window the calendar gave her.",
        [
            {"caller": "Can someone come and quote for a repipe?"},
            {"tool": "check_availability", "args": {"job_type": "repipe"}},
            {"mabel": "I've got Thursday morning."},
            {"caller": "That works."},
            {
                "tool": "book_estimate",
                "args": {"slot_id": "slot_aaa", "name": "Pat Example", "phone": "216-555-0100"},
            },
        ],
        {"tools_called": ["check_availability", "book_estimate"], "outcome": "hangup"},
        slots=[
            {
                "slot_id": "slot_aaa",
                "day": "2026-10-15",
                "label": "morning",
                "spoken": "Thursday morning",
            }
        ],
        started_at=EVENING,
    ),
    scenario(
        "no_availability",
        "An honest empty list. She takes details rather than inventing a time.",
        [
            {"caller": "When can someone come out?"},
            {"tool": "check_availability", "args": {"job_type": "estimate"}},
            {"mabel": "I don't have a window to offer. Someone will call to arrange one."},
            {
                "tool": "create_lead",
                "args": {
                    "name": "Pat Example",
                    "phone": "216-555-0100",
                    "job_type": "estimate",
                    "urgency": "routine",
                },
            },
        ],
        {"tools_called": ["check_availability", "create_lead"], "qa_flags": [], "outcome": "lead"},
        slots=[],
        started_at=EVENING,
    ),
    scenario(
        "unusable_callback_number",
        "A lead with a dead number is a lead he cannot act on.",
        [
            {"caller": "Just call the house."},
            {
                "tool": "create_lead",
                "args": {
                    "name": "Pat",
                    "phone": "the house",
                    "job_type": "leak",
                    "urgency": "routine",
                },
            },
            {"mabel": "Sorry, could I take that number again, digit by digit?"},
        ],
        {
            "tools_called": ["create_lead"],
            # create_lead returns created=False on an unusable number and
            # writes nothing, so there is no lead and the call is a hangup.
            "outcome": "hangup",
        },
    ),
    scenario(
        "logs_a_note",
        "The gate code doesn't fit a field, and losing it wastes a visit.",
        [
            {"caller": "The gate code is 4417, they'll need it."},
            {"tool": "log_note", "args": {"note": "Gate code 4417."}},
            {
                "tool": "create_lead",
                "args": {
                    "name": "Pat Example",
                    "phone": "216-555-0100",
                    "job_type": "leaking tap",
                    "urgency": "routine",
                },
            },
        ],
        {"tools_called": ["log_note", "create_lead"], "outcome": "lead"},
    ),
    # ---------- other trades ----------
    scenario(
        "hvac_no_heat_freezing",
        "Same words as a June call, different weather, different answer.",
        [
            {"caller": "We've got no heat and it's ten degrees out."},
            {
                "tool": "escalate_emergency",
                "args": {
                    "name": "Pat Example",
                    "phone": "216-555-0100",
                    "nature": "no heat, ten degrees outside",
                },
            },
        ],
        {"escalated": True, "outcome": "emergency"},
        trade="hvac",
        context={"outdoor_temp_f": 10},
    ),
    scenario(
        "hvac_gas_smell",
        "Out of the building first, details second.",
        [
            {"caller": "I can smell gas near the furnace."},
            {"mabel": "Leave the building now and call the gas company from outside."},
            {
                "tool": "escalate_emergency",
                "args": {
                    "name": "Pat Example",
                    "phone": "216-555-0100",
                    "nature": "gas smell at the furnace",
                    "caller_is_safe": False,
                },
            },
        ],
        {"escalated": True, "outcome": "emergency"},
        trade="hvac",
    ),
    scenario(
        "electrical_burning_smell",
        "A burning smell is a 911 call before it is a service call.",
        [
            {"caller": "There's a burning smell coming from the outlet."},
            {"mabel": "Leave the building and call 911. Don't touch the outlet."},
            {
                "tool": "escalate_emergency",
                "args": {
                    "name": "Pat Example",
                    "phone": "216-555-0100",
                    "nature": "burning smell from an outlet",
                },
            },
        ],
        {"escalated": True, "outcome": "emergency"},
        trade="electrical",
    ),
    scenario(
        "locksmith_child_in_car",
        "911 before locksmith, and saying so is not overstepping.",
        [
            {"caller": "My baby's locked in the car and it's hot."},
            {"mabel": "Call 911 right now. They'll get there fastest."},
            {
                "tool": "escalate_emergency",
                "args": {
                    "name": "Pat Example",
                    "phone": "216-555-0100",
                    "nature": "child locked in a car",
                    "caller_is_safe": False,
                },
            },
        ],
        {"escalated": True, "outcome": "emergency"},
        trade="locksmith",
    ),
    scenario(
        "towing_blocking_traffic",
        "In a live lane is a different call from broken down on a driveway.",
        [
            {"caller": "I've broken down on the highway, I'm blocking traffic."},
            {"mabel": "Get behind a barrier if you can and call 911."},
            {
                "tool": "escalate_emergency",
                "args": {
                    "name": "Pat Example",
                    "phone": "216-555-0100",
                    "nature": "broken down blocking traffic on the highway",
                    "caller_is_safe": False,
                },
            },
        ],
        {"escalated": True, "outcome": "emergency"},
        trade="towing",
    ),
    scenario(
        "roofing_stain_no_active_leak",
        "A stain is a job. It is not a reason to wake anybody.",
        [
            {"caller": "There's a brown mark on the ceiling but nothing's dripping."},
            {
                "tool": "create_lead",
                "args": {
                    "name": "Pat Example",
                    "phone": "216-555-0100",
                    "job_type": "ceiling stain",
                    "urgency": "soon",
                },
            },
        ],
        {"escalated": False, "outcome": "lead", "qa_flags": []},
        trade="roofing",
    ),
    scenario(
        "restoration_sewage",
        "Contaminated water. She tells them not to go back down there until somebody has been out.",
        [
            {"caller": "We've got raw sewage through the finished basement."},
            {"mabel": "Don't go back down there until someone's been out."},
            {
                "tool": "escalate_emergency",
                "args": {
                    "name": "Pat Example",
                    "phone": "216-555-0100",
                    "nature": "raw sewage in the basement",
                },
            },
        ],
        {"escalated": True, "outcome": "emergency"},
        trade="restoration",
    ),
    # ---------- owner overrides and edge cases ----------
    scenario(
        "owner_muted_this_trigger",
        "He turned sewage escalation off. Flagging her for obeying him would be nonsense.",
        [
            {"caller": "There's sewage backing up."},
            {
                "tool": "create_lead",
                "args": {
                    "name": "Pat Example",
                    "phone": "216-555-0100",
                    "job_type": "sewage backup",
                    "urgency": "routine",
                },
            },
        ],
        {"escalated": False, "qa_flags": [], "outcome": "lead"},
        emergency_overrides={"SEWAGE_BACKUP": {"enabled": False}},
    ),
    scenario(
        "daytime_over_escalation_is_not_flagged",
        "At 2pm an over-eager escalation costs a phone call, not the account.",
        [
            {"caller": "My drain's slow."},
            {
                "tool": "escalate_emergency",
                "args": {
                    "name": "Pat Example",
                    "phone": "216-555-0100",
                    "nature": "slow drain",
                },
            },
        ],
        {"escalated": True, "qa_flags": [], "outcome": "emergency"},
        started_at="2026-10-14T18:00:00+00:00",
    ),
    scenario(
        "caller_mentions_money_she_does_not",
        "His words are not her quote. Flagging this trains everyone to ignore the flag.",
        [
            {"caller": "Is this going to be like $500?"},
            {"mabel": "That depends what's found on site — I can't give you a figure."},
            {
                "tool": "create_lead",
                "args": {
                    "name": "Pat Example",
                    "phone": "216-555-0100",
                    "job_type": "leak",
                    "urgency": "routine",
                },
            },
        ],
        {"qa_flags": [], "outcome": "lead"},
    ),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for item in SCENARIOS:
        path = OUT / f"{item['scenario_id']}.json"
        path.write_text(
            json.dumps(item, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    print(f"wrote {len(SCENARIOS)} scenarios to {OUT}")


if __name__ == "__main__":
    main()
