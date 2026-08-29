"""Money is integer cents. This file is the fence around invariant 5."""

from __future__ import annotations

from decimal import Decimal

import pytest

from mabel_domain.money import (
    MAX_OWNER_AMOUNT_CENTS,
    Money,
    MoneyError,
    cents_from_decimal,
    parse_owner_amount,
    sum_cents,
)


class TestMoneyRefusesFloat:
    def test_float_is_not_money(self):
        with pytest.raises(MoneyError, match="integer cents"):
            Money(38.0)  # type: ignore[arg-type]

    def test_decimal_is_not_money_either(self):
        # Decimal is exact, but it is still not cents. Convert explicitly.
        with pytest.raises(MoneyError, match="integer cents"):
            Money(Decimal(38))  # type: ignore[arg-type]

    def test_bool_is_not_money(self):
        # bool is an int subclass, which is exactly the kind of thing that
        # slips through an isinstance check and stores a job worth $0.01.
        with pytest.raises(MoneyError, match="integer cents"):
            Money(True)  # type: ignore[arg-type]

    def test_string_is_not_money(self):
        with pytest.raises(MoneyError):
            Money("3800")  # type: ignore[arg-type]


class TestFormatting:
    @pytest.mark.parametrize(
        ("cents", "expected"),
        [
            (0, "$0.00"),
            (5, "$0.05"),
            (100, "$1.00"),
            (1_460_000, "$14,600.00"),
            (-2550, "-$25.50"),
        ],
    )
    def test_format(self, cents, expected):
        assert Money(cents).format() == expected

    def test_format_whole_drops_empty_cents(self):
        # The dashboard and a 160-character SMS both want `$14,600`.
        assert Money(1_460_000).format_whole() == "$14,600"

    def test_format_whole_keeps_real_cents(self):
        assert Money(1_460_050).format_whole() == "$14,600.50"

    def test_non_usd_carries_its_code(self):
        assert Money(1000, "CAD").format() == "10.00 CAD"


class TestArithmetic:
    def test_addition_stays_integer(self):
        total = Money(1999) + Money(1)
        assert total.cents == 2000
        assert isinstance(total.cents, int)

    def test_currencies_do_not_mix(self):
        with pytest.raises(MoneyError, match="cannot combine"):
            Money(100, "USD") + Money(100, "CAD")

    def test_sum_cents_ignores_nulls(self):
        # `leads.value_cents` is nullable — the owner has not priced it yet.
        # An unpriced job contributes nothing, it does not blow up the report.
        assert sum_cents([380_000, None, 120_000, None]) == 500_000

    def test_sum_of_nothing_is_zero(self):
        assert sum_cents([]) == 0


class TestOwnerAmount:
    """The one place a human dollar figure enters the system: the owner
    texting `WON RUIZ 3800`."""

    @pytest.mark.parametrize(
        ("raw", "cents"),
        [
            ("3800", 380_000),
            ("$3800", 380_000),
            ("3,800", 380_000),
            ("3800.50", 380_050),
            ("3800.5", 380_050),
            ("0", 0),
            ("  450  ", 45_000),
        ],
    )
    def test_bare_digits_are_whole_dollars(self, raw, cents):
        assert parse_owner_amount(raw).cents == cents

    @pytest.mark.parametrize(
        "raw",
        [
            "thirty eight hundred",
            "",
            "38 00",
            "1e3",
            "$",
            "3800.999",
            "-500",
            "about 3800",
            "3800 or so",
        ],
    )
    def test_anything_ambiguous_is_refused(self, raw):
        # Refusing means the SMS handler asks him to repeat it. Guessing means
        # a wrong number on the monthly report he judges us by.
        with pytest.raises(MoneyError):
            parse_owner_amount(raw)

    def test_fat_finger_is_capped(self):
        with pytest.raises(MoneyError, match="cap"):
            parse_owner_amount(str(MAX_OWNER_AMOUNT_CENTS // 100 + 1))


class TestDecimalConversion:
    def test_half_up_like_an_invoice(self):
        assert cents_from_decimal(Decimal("10.005")).cents == 1001

    def test_rejects_float(self):
        with pytest.raises(MoneyError, match="expected Decimal"):
            cents_from_decimal(10.5)  # type: ignore[arg-type]
