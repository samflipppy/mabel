"""E.164 normalisation. Tenant resolution depends on this being exact —
two spellings of one number must not become two tenants."""

from __future__ import annotations

import pytest

from mabel_domain.phone import (
    PhoneError,
    format_national,
    last_ten,
    normalize_e164,
    try_normalize_e164,
)


class TestNormalisation:
    @pytest.mark.parametrize(
        "raw",
        [
            "+12165550148",
            "2165550148",
            "216-555-0148",
            "(216) 555-0148",
            "216.555.0148",
            "12165550148",
            "+1 (216) 555-0148",
            "  +1 216 555 0148  ",
        ],
    )
    def test_every_spelling_lands_on_one_number(self, raw):
        assert normalize_e164(raw) == "+12165550148"

    def test_international_passes_through_if_already_e164(self):
        assert normalize_e164("+442071838750") == "+442071838750"

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "555-0148",  # no area code
            "0165550148",  # NANP area codes do not start with 0
            "2160550148",  # nor do exchanges
            "5550148",
            "not a phone",
            "+0123456789",
            "12345678901234567890",
        ],
    )
    def test_ambiguous_input_raises_rather_than_guessing(self, raw):
        with pytest.raises(PhoneError):
            normalize_e164(raw)

    def test_non_string_raises(self):
        with pytest.raises(PhoneError, match="expected a string"):
            normalize_e164(2165550148)  # type: ignore[arg-type]


class TestTryNormalise:
    def test_returns_none_for_garbage(self):
        assert try_normalize_e164("unknown") is None

    def test_returns_none_for_none(self):
        assert try_normalize_e164(None) is None

    def test_still_normalises_the_good_case(self):
        assert try_normalize_e164("216-555-0148") == "+12165550148"


class TestHelpers:
    def test_last_ten_for_fuzzy_matching(self):
        assert last_ten("+12165550148") == "2165550148"

    def test_format_national_for_humans(self):
        assert format_national("+12165550148") == "(216) 555-0148"

    def test_format_national_leaves_foreign_numbers_alone(self):
        assert format_national("+442071838750") == "+442071838750"
