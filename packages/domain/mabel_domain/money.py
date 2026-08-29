"""Money. Integer cents, always, with the currency carried alongside.

Invariant 5: money is integer cents in BIGINT with an explicit currency
column. Never float. Stripe speaks cents, and so do we, end to end.

Invariant 4: no LLM output ever becomes a dollar figure. `Money` can only be
built from an integer. There is deliberately no `from_string` that a
transcript could be fed into — the one place a human dollar figure enters the
system is the owner typing it, and that goes through `parse_owner_amount`,
which is strict, bounded, and refuses anything it does not fully understand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# A job worth more than a million dollars is a typo, not a job. The cap is here
# so a fat-fingered SMS ("WON RUIZ 380000000") is rejected rather than becoming
# the headline number on a monthly report.
MAX_OWNER_AMOUNT_CENTS = 100_000_000


class MoneyError(ValueError):
    """Raised when something that is not money is offered as money."""


@dataclass(frozen=True, slots=True)
class Money:
    cents: int
    currency: str = "USD"

    def __post_init__(self) -> None:
        if isinstance(self.cents, bool) or not isinstance(self.cents, int):
            raise MoneyError(f"money must be integer cents, got {type(self.cents).__name__}")
        if not self.currency.isalpha() or len(self.currency) != 3:
            raise MoneyError(f"currency must be a 3-letter code, got {self.currency!r}")
        object.__setattr__(self, "currency", self.currency.upper())

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.cents + other.cents, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.cents - other.cents, self.currency)

    def _same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise MoneyError(f"cannot combine {self.currency} and {other.currency}")

    @property
    def is_zero(self) -> bool:
        return self.cents == 0

    def format(self) -> str:
        """Render for a human. The only place cents become a string with a
        dollar sign on it, and it is deterministic code doing it."""
        if self.currency != "USD":
            return f"{self.cents / 100:,.2f} {self.currency}"
        sign = "-" if self.cents < 0 else ""
        whole, part = divmod(abs(self.cents), 100)
        return f"{sign}${whole:,}.{part:02d}"

    def format_whole(self) -> str:
        """Dashboard and SMS form: `$14,600`. Drops cents when they are zero,
        because an owner reading a 160-character text does not need `.00`."""
        if self.currency == "USD" and self.cents % 100 == 0:
            sign = "-" if self.cents < 0 else ""
            return f"{sign}${abs(self.cents) // 100:,}"
        return self.format()


ZERO_USD = Money(0)


def sum_cents(amounts: list[int | None]) -> int:
    """Total a column of nullable cents. Deterministic, integer throughout."""
    return sum(a for a in amounts if a is not None)


_OWNER_AMOUNT = re.compile(r"^\$?(\d{1,3}(?:,\d{3})*|\d{1,9})(?:\.(\d{1,2}))?$")


def parse_owner_amount(raw: str, *, currency: str = "USD") -> Money:
    """Parse a dollar figure a human typed, e.g. the `3800` in `WON RUIZ 3800`.

    Strict on purpose. Bare digits are read as whole dollars, because that is
    what an owner means when he texts `3800`. A decimal part is read as cents.
    Anything else raises, and the SMS handler asks him to repeat it rather than
    guessing at the number that drives his monthly report.
    """
    match = _OWNER_AMOUNT.match(raw.strip())
    if match is None:
        raise MoneyError(f"not a plain dollar amount: {raw!r}")
    dollars, fraction = match.group(1), match.group(2)
    cents = int(dollars.replace(",", "")) * 100
    if fraction is not None:
        cents += int(fraction.ljust(2, "0"))
    if cents > MAX_OWNER_AMOUNT_CENTS:
        raise MoneyError(
            f"amount above the {Money(MAX_OWNER_AMOUNT_CENTS).format_whole()} cap: {raw!r}"
        )
    return Money(cents, currency)


def cents_from_decimal(value: Decimal, *, currency: str = "USD") -> Money:
    """Convert a NUMERIC read out of Postgres into cents. Used for the one
    NUMERIC column in the schema that touches a cost calculation
    (`usage_daily.voice_minutes`, which is minutes, not money) and for Stripe
    reconciliation. Half-up, because that is what an invoice does.
    """
    if not isinstance(value, Decimal):
        raise MoneyError(f"expected Decimal, got {type(value).__name__}")
    quantized = (value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return Money(int(quantized), currency)
