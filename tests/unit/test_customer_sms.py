"""The customer-facing templates, and the three rules they all obey.

The rules are asserted against *every* builder by reflection rather than one
test per function. A new template added to `customer.py` is picked up here
automatically, which is the point: the failure mode worth defending against is
not one of these six messages being wrong, it is the seventh one being written
in six months by someone who has not read the module docstring.
"""

from __future__ import annotations

import inspect
import re
from datetime import datetime

import pytest

from mabel_sms import customer
from mabel_sms.compose import SEGMENT

# Every public builder in the module, discovered rather than listed.
BUILDERS = [
    (name, fn)
    for name, fn in vars(customer).items()
    if inspect.isfunction(fn) and not name.startswith("_") and fn.__module__ == customer.__name__
]

# Arguments good enough to render each builder. Anything not named here gets
# its default, so a new builder with all-optional arguments needs no entry.
SAMPLE_ARGS: dict[str, dict] = {
    "call_confirmation": {
        "business_name": "Ruiz Plumbing",
        "job_type": "water heater leak",
        "service_address": "44 Elm St",
    },
    "emergency_acknowledgement": {"business_name": "Ruiz Plumbing", "alerted": True},
    "after_hours_confirmation": {
        "business_name": "Ruiz Plumbing",
        "opens_at": datetime(2026, 8, 30, 8, 0),
    },
    "missed_call": {"business_name": "Ruiz Plumbing"},
    "review_request": {
        "business_name": "Ruiz Plumbing",
        "review_url": "https://g.page/r/abc/review",
        "customer_name": "Dana",
    },
    "stop_confirmation": {"business_name": "Ruiz Plumbing"},
}


def render(name, fn) -> str:
    return fn(**SAMPLE_ARGS.get(name, {"business_name": "Ruiz Plumbing"}))


def test_every_builder_has_sample_arguments():
    """Guards the reflection above. A builder nobody rendered is a builder
    none of the rules below were actually checked against."""
    missing = [name for name, _ in BUILDERS if name not in SAMPLE_ARGS]
    assert not missing, f"add sample arguments for {missing} so the rules cover them"


@pytest.mark.parametrize("name,fn", BUILDERS, ids=[n for n, _ in BUILDERS])
def test_no_customer_message_contains_money(name, fn):
    """A figure sent to a customer is a figure they will hold the business to.

    Catches `$40`, `40 dollars`, `40.00` and bare four-figure numbers. The
    review URL is stripped first, because an ID inside a link is not a price
    and a test that fails on one teaches people to weaken the test.
    """
    body = re.sub(r"https?://\S+", "", render(name, fn))
    assert "$" not in body
    assert not re.search(r"\b\d+\.\d{2}\b", body)
    assert not re.search(r"\bdollars?\b|\bcents?\b|\bprice\b|\bquote\b|\bfee\b", body, re.I)


@pytest.mark.parametrize("name,fn", BUILDERS, ids=[n for n, _ in BUILDERS])
def test_no_customer_message_promises_a_truck(name, fn):
    """Mabel cannot know that anyone is moving, so she never says it.

    `enqueue_emergency` returning False is exactly the case where a shop has an
    emergency and nobody on call; a message that said "on the way" there would
    be a lie told at the worst possible moment.
    """
    body = render(name, fn).lower()
    for phrase in ("on the way", "on our way", "en route", "dispatched", "heading over"):
        assert phrase not in body, f"{name} promises a truck: {phrase!r}"


@pytest.mark.parametrize("name,fn", BUILDERS, ids=[n for n, _ in BUILDERS])
def test_every_message_names_the_business_first(name, fn):
    """A stranger's phone shows an unknown number. A message that does not open
    with who it is from reads as spam, and spam reports kill the campaign."""
    assert render(name, fn).startswith("Ruiz Plumbing")


@pytest.mark.parametrize("name,fn", BUILDERS, ids=[n for n, _ in BUILDERS])
def test_every_message_fits_one_segment(name, fn):
    assert len(render(name, fn)) <= SEGMENT


@pytest.mark.parametrize("name,fn", BUILDERS, ids=[n for n, _ in BUILDERS])
def test_every_message_is_gsm7(name, fn):
    """One non-GSM-7 character switches the whole message to UCS-2 and halves
    the segment length, turning a 158-character text into three parts."""
    assert customer.to_gsm7(render(name, fn)) == render(name, fn)


def test_the_first_message_carries_opt_out_language_and_later_ones_do_not():
    first = customer.missed_call(business_name="Ruiz Plumbing", first_contact=True)
    later = customer.missed_call(business_name="Ruiz Plumbing", first_contact=False)
    assert "STOP" in first
    assert "STOP" not in later
    # The saving is the point: repeating it costs a fifth of every segment.
    assert len(later) < len(first)


def test_opt_out_language_survives_a_body_that_runs_long():
    """The body is fitted to the space the footer leaves, so an over-long body
    loses its own words rather than the legally required ones.

    Fitting the two together instead trims from the end -- and the end is the
    footer, so a long enough service address silently dropped the opt-out
    language. That is what the first version of this did."""
    message = customer.call_confirmation(
        business_name="Ruiz Plumbing",
        job_type="water heater leak" * 10,
        service_address="44 Elm Street, Apartment 3B, Cleveland Heights",
    )
    assert len(message) <= SEGMENT
    assert message.endswith("Reply STOP to opt out.")


def test_the_two_emergency_branches_say_different_things():
    """The distinction is the feature. Collapsing them would tell a caller with
    a gas leak to wait for a callback that has nobody behind it."""
    alerted = customer.emergency_acknowledgement(business_name="Ruiz", alerted=True)
    nobody = customer.emergency_acknowledgement(business_name="Ruiz", alerted=False)
    assert "alerted" in alerted
    assert "another provider" in nobody
    assert "911" in alerted and "911" in nobody


def test_a_review_request_drops_prose_before_it_drops_the_link():
    """A truncated link is not a shorter message, it is a broken one."""
    url = "https://search.google.com/local/writereview?placeid=" + "A" * 20
    message = customer.review_request(
        business_name="Delgado Heating and Air",
        review_url=url,
        customer_name="Bartholomew Featherstonehaugh",
    )
    assert url in message
    assert len(message) <= SEGMENT
    # The pleasantries are what went, not the address.
    assert "If we did right by you" not in message


def test_a_review_request_runs_to_two_segments_rather_than_break_the_link():
    """The only message in the codebase allowed over one segment, and the
    reason is that its entire payload is a URL. Nothing about a review request
    is urgent enough to be worth arriving intact in one part."""
    url = "https://search.google.com/local/writereview?placeid=" + "A" * 90
    message = customer.review_request(
        business_name="Delgado Heating and Air Conditioning of Greater Cleveland",
        review_url=url,
    )
    assert url in message
    assert len(message) > SEGMENT


def test_a_review_request_cannot_be_sent_as_a_first_contact():
    """Structural, not conventional. There is no argument to get wrong: the
    builder has no `first_contact` parameter, so cold marketing to a stranger
    is not one keyword away."""
    assert "first_contact" not in inspect.signature(customer.review_request).parameters
