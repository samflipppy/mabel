"""The messages the *caller* receives.

Everything in `compose.py` is addressed to the owner. This file is addressed to
a homeowner who phoned a business ninety seconds ago, and the difference is not
cosmetic.

**They do not know who we are.** The owner has our number saved; a customer
sees an unfamiliar number on an unfamiliar day. Every message here names the
business in the first few characters, because a message that opens with
anything else reads as spam and gets reported as spam, and reports are what
kill a 10DLC campaign.

**No message here contains money.** Not a quote, not an estimate, not a callout
fee. A figure sent to a customer is a figure they will hold the business to,
and the only place figures exist is columns an owner typed into.
`test_customer_sms.py` asserts this against every builder rather than trusting
the reading.

**No message here promises a truck.** "Someone is on the way" is a sentence
Mabel is never allowed to produce, because she cannot know it -- the on-call
tech may be asleep, forty minutes out, or on another job. The strongest form
allowed is what was actually done: we alerted them. `enqueue_emergency` already
returns False when there is nobody to alert, and the wording here tracks that
distinction.

Pure. Takes values, returns strings.
"""

from __future__ import annotations

from datetime import datetime

from mabel_sms.compose import SEGMENT, fit, to_gsm7

# Appended to the first message a contact ever gets from a tenant, and not
# afterwards. Carriers expect opt-out language to be discoverable, not on every
# message; repeating it costs a fifth of the segment each time.
OPT_OUT = " Reply STOP to opt out."


def _msg(body: str, *, first_contact: bool) -> str:
    """One segment, GSM-7, with opt-out language when it is owed.

    The body is fitted to the space the footer leaves and the footer is then
    appended, rather than fitting the two together. Fitting the whole string
    trims from the end, and the end is the footer -- so a long service address
    silently removed the opt-out language, which is the one part of the message
    that is not ours to drop. That is what the first version of this did.
    """
    tail = OPT_OUT if first_contact else ""
    return fit(to_gsm7(body.strip()), SEGMENT - len(tail)) + tail


def call_confirmation(
    *,
    business_name: str,
    job_type: str | None = None,
    service_address: str | None = None,
    callback_window: str = "shortly",
    first_contact: bool = True,
) -> str:
    """Sent within seconds of the call ending. The highest-value message here.

    A caller with nothing in their hand phones the next three businesses on the
    search results while they wait. A caller holding a text with their own
    address in it has already decided.

    `job_type` and `service_address` are read back deliberately: they are the
    two things a caller worries an AI got wrong, and seeing them correct is
    what converts scepticism into trust.
    """
    what = f" about the {job_type}" if job_type else ""
    where = f" at {service_address}" if service_address else ""
    body = (
        f"{business_name}: thanks for calling. We have your request"
        f"{what}{where} and will call you back {callback_window}."
    )
    return _msg(body, first_contact=first_contact)


def emergency_acknowledgement(
    *, business_name: str, alerted: bool, first_contact: bool = True
) -> str:
    """After an escalation. `alerted` is the return of `enqueue_emergency`.

    The two branches say materially different things and must not be collapsed.
    True means a human's phone rang. False means nobody was on call, and the
    honest message tells the caller to find another route rather than wait on a
    callback with no one behind it. For a gas leak or a downed line that
    difference is the entire point of the feature.
    """
    if alerted:
        body = (
            f"{business_name}: we have your emergency and the on-call tech has "
            f"been alerted. If this is life-threatening call 911."
        )
    else:
        body = (
            f"{business_name}: we have your emergency but cannot reach anyone "
            f"right now. Please try another provider. If life-threatening call 911."
        )
    return _msg(body, first_contact=first_contact)


def after_hours_confirmation(
    *, business_name: str, opens_at: datetime | None = None, first_contact: bool = True
) -> str:
    """Non-urgent, out of hours. Sets an expectation rather than implying one.

    `opens_at` is a tenant-local datetime computed from business hours, not a
    string a model produced.
    """
    when = f" when we open at {_clock(opens_at)}" if opens_at else " first thing tomorrow"
    body = (
        f"{business_name}: thanks for calling after hours. We have your request "
        f"and will call you{when}."
    )
    return _msg(body, first_contact=first_contact)


def missed_call(*, business_name: str, first_contact: bool = True) -> str:
    """The call never reached Mabel -- caller hung up, or the leg failed.

    Worth sending precisely because it is the case where the business has no
    other record that this person existed. There is no lead, no transcript and
    no name; there is a number, and ninety seconds in which a reply still reads
    as service rather than cold outreach.
    """
    body = (
        f"{business_name}: sorry we missed your call. Reply to this text and we "
        f"will get right back to you."
    )
    return _msg(body, first_contact=first_contact)


def review_request(*, business_name: str, review_url: str, customer_name: str | None = None) -> str:
    """Sent days after the owner marked a job won. Never on the call path.

    Deliberately has no `first_contact` parameter. A review request can only
    follow a completed job, so by definition it is not the first message -- and
    a builder that could send cold marketing to a stranger should not exist in
    a shape where that is one wrong argument away.
    """
    who = f" {customer_name}" if customer_name else ""
    body = to_gsm7(
        f"{business_name}: thanks{who} for your business. "
        f"If we did right by you a quick review helps a lot: {review_url}"
    )
    if len(body) <= SEGMENT:
        return body

    # Too long. Drop the pleasantries first -- they are the cheapest words here.
    body = to_gsm7(f"{business_name}: thanks for your business. Review us here: {review_url}")
    if len(body) <= SEGMENT:
        return body

    # Still too long, which means a long business name and a long review URL.
    # This returns two segments rather than one, deliberately and as the only
    # place in the codebase that does.
    #
    # `fit` would trim the tail, and the tail is the link. A review request
    # with a truncated URL is not a shorter review request, it is a broken one
    # -- and unlike the 3am emergency, nothing about this message is urgent
    # enough to be worth arriving intact in one part. Cost over correctness is
    # the wrong trade for the message whose entire payload is a URL.
    return body


def stop_confirmation(*, business_name: str) -> str:
    """Compliance, from the customer's side. No attempt to talk them out of it.

    Names the business, so someone who deals with several contractors knows
    which one they have just silenced.
    """
    return fit(to_gsm7(f"{business_name}: you will not receive further texts from us."), SEGMENT)


def _clock(when: datetime) -> str:
    """8am, 8:30am.

    Hand-rolled rather than strftime because `%-I` is a glibc extension that is
    not available on Windows -- the same reason `compose._supports_dash_l`
    exists.
    """
    hour = when.hour % 12 or 12
    meridiem = "am" if when.hour < 12 else "pm"
    return f"{hour}:{when.minute:02d}{meridiem}" if when.minute else f"{hour}{meridiem}"
