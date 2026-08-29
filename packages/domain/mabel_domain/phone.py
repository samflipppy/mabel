"""Phone numbers. E.164, normalised once, at the edge.

Pure. This is the type that lets tenant resolution be honest: the dialed
number arrives as a SIP To header, gets normalised here, and is looked up
against `tenants.did_e164`. If normalisation is loose, two spellings of the
same number resolve to two different tenants, or to none.

North American numbers get the full treatment because every customer today is
NANP. Everything else is accepted only if it already looks like E.164, rather
than guessed at.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema


class PhoneError(ValueError):
    """Raised when something that is not a phone number is offered as one."""


_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_DIGITS = re.compile(r"\D")

# Common ways a human writes a US number into the portal or an SMS.
_NANP_10 = re.compile(r"^\d{10}$")
_NANP_11 = re.compile(r"^1\d{10}$")


def normalize_e164(raw: str) -> str:
    """Normalise to E.164 or raise.

    `(216) 555-0148`, `216-555-0148`, `12165550148` and `+1 216 555 0148` all
    become `+12165550148`. Anything ambiguous raises rather than guessing —
    a wrong callback number is a lost job.
    """
    if not isinstance(raw, str):
        raise PhoneError(f"expected a string, got {type(raw).__name__}")
    candidate = raw.strip()
    if not candidate:
        raise PhoneError("empty phone number")

    if candidate.startswith("+"):
        compact = "+" + _DIGITS.sub("", candidate)
        if not _E164.match(compact):
            raise PhoneError(f"not a valid E.164 number: {raw!r}")
        return compact

    digits = _DIGITS.sub("", candidate)
    if _NANP_10.match(digits):
        # A NANP area code and exchange both start 2-9. Enforcing it rejects
        # `0000000000` and the like rather than storing a number that can
        # never be dialled.
        if digits[0] in "01" or digits[3] in "01":
            raise PhoneError(f"not a dialable NANP number: {raw!r}")
        return "+1" + digits
    if _NANP_11.match(digits):
        return normalize_e164(digits[1:])

    raise PhoneError(f"cannot normalise to E.164 without a country code: {raw!r}")


def try_normalize_e164(raw: str | None) -> str | None:
    """Normalise, or return None. For fields where a bad number should be
    dropped rather than fail the whole write — a caller ID that arrived
    garbled, say. Never use this on a callback number the owner needs."""
    if raw is None:
        return None
    try:
        return normalize_e164(raw)
    except PhoneError:
        return None


def last_ten(e164: str) -> str:
    """The last ten digits, for fuzzy contact matching against numbers stored
    by an integration in some other format. Matching helper only — never the
    key we resolve a tenant by."""
    return _DIGITS.sub("", e164)[-10:]


def format_national(e164: str) -> str:
    """`+12165550148` -> `(216) 555-0148`. For display and for SMS to an
    owner, who does not read E.164."""
    digits = _DIGITS.sub("", e164)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return e164
    return f"({digits[0:3]}) {digits[3:6]}-{digits[6:]}"


class _E164Type(str):
    """Pydantic-aware E.164 string. Validating on the way in means a model
    holding an `E164` is holding a normalised number, everywhere, always."""

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: type[Any], handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_after_validator_function(
            cls._validate,
            core_schema.str_schema(),
            serialization=core_schema.to_string_ser_schema(),
        )

    @staticmethod
    def _validate(value: str) -> str:
        return normalize_e164(value)


E164 = Annotated[str, _E164Type]
