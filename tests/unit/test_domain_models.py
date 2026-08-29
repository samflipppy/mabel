"""Domain model invariants — the ones a careless caller would otherwise
violate silently."""

from __future__ import annotations

from datetime import UTC, datetime, time
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mabel_domain.enums import LeadStatus
from mabel_domain.hours import in_quiet_hours, is_after_hours, is_open
from mabel_domain.models import AgentConfig, BusinessHours, DayHours, Integration, Lead, Tenant

NOW = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)

WEEKDAYS_8_TO_5 = BusinessHours(
    mon=DayHours(open=time(8), close=time(17)),
    tue=DayHours(open=time(8), close=time(17)),
    wed=DayHours(open=time(8), close=time(17)),
    thu=DayHours(open=time(8), close=time(17)),
    fri=DayHours(open=time(8), close=time(17)),
)


def _config(**overrides) -> AgentConfig:
    base = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "version": 1,
        "greeting": "Thanks for calling Ruiz Plumbing.",
        "business_hours": WEEKDAYS_8_TO_5,
        "created_at": NOW,
    }
    return AgentConfig(**(base | overrides))


class TestTenantTimezone:
    def test_iana_zone_required(self):
        with pytest.raises(ValidationError, match="not an IANA timezone"):
            Tenant(
                id=uuid4(),
                business_name="Ruiz Plumbing",
                trade="plumbing",
                timezone="EST5EDT-ish",
                created_at=NOW,
            )

    def test_a_real_zone_is_accepted(self):
        tenant = Tenant(
            id=uuid4(),
            business_name="Ruiz Plumbing",
            trade="plumbing",
            timezone="America/Denver",
            created_at=NOW,
        )
        assert tenant.timezone == "America/Denver"

    def test_did_is_normalised_on_the_way_in(self):
        tenant = Tenant(
            id=uuid4(),
            business_name="Ruiz Plumbing",
            trade="plumbing",
            did_e164="(216) 555-0148",
            created_at=NOW,
        )
        assert tenant.did_e164 == "+12165550148"


class TestAgentConfig:
    def test_price_cannot_be_removed_from_never_say(self):
        # A tenant may add to never_say. Removing `price` is not theirs to do.
        with pytest.raises(ValidationError, match="never_say must always contain"):
            _config(never_say=["arrival_time"])

    def test_a_tenant_may_add_their_own(self):
        config = _config(never_say=["price", "warranty_terms"])
        assert "warranty_terms" in config.never_say

    @pytest.mark.parametrize("rate", [0.4, 2.1, 0.0])
    def test_speaking_rate_stays_intelligible(self, rate):
        with pytest.raises(ValidationError, match="speaking_rate"):
            _config(speaking_rate=rate)

    def test_unknown_field_is_rejected(self):
        # extra="forbid" everywhere: a typo'd field name should fail loudly,
        # not silently drop the value the portal just saved.
        with pytest.raises(ValidationError):
            _config(greetinggg="oops")


class TestLead:
    def _lead(self, **overrides) -> Lead:
        base = {
            "id": uuid4(),
            "tenant_id": uuid4(),
            "created_at": NOW,
            "updated_at": NOW,
        }
        return Lead(**(base | overrides))

    def test_won_requires_won_at(self):
        with pytest.raises(ValidationError, match="won_at"):
            self._lead(status=LeadStatus.WON)

    def test_won_with_a_timestamp_is_fine(self):
        lead = self._lead(status=LeadStatus.WON, won_at=NOW, value_cents=1_460_000)
        assert lead.value_cents == 1_460_000

    def test_untouched_is_the_nudge_condition(self):
        assert self._lead().is_untouched is True
        assert self._lead(first_touched_at=NOW).is_untouched is False


class TestIntegrationNeverHoldsCredentials:
    @pytest.mark.parametrize(
        "key", ["access_token", "refresh_token", "client_secret", "api_key", "password"]
    )
    def test_a_token_in_config_is_refused(self, key):
        # Tokens live in the Supabase vault under `vault_key`. We hold the key
        # name, never the secret.
        with pytest.raises(ValidationError, match="vault"):
            Integration(
                id=uuid4(),
                tenant_id=uuid4(),
                provider="jobber",
                config={key: "whatever"},
                created_at=NOW,
            )

    def test_ordinary_config_is_fine(self):
        integration = Integration(
            id=uuid4(),
            tenant_id=uuid4(),
            provider="jobber",
            config={"default_request_title": "After-hours call"},
            vault_key="tenant/abc/jobber",
            created_at=NOW,
        )
        assert integration.vault_key == "tenant/abc/jobber"


class TestAfterHours:
    """Computed, never stored. Same instant, two tenants, two answers."""

    def test_same_instant_differs_by_zone(self):
        # 2026-10-14 15:00 UTC is 11:00 in Cleveland and 08:00 in Los Angeles.
        # Both are open. At 14:00 UTC it is 07:00 in LA — closed.
        early = datetime(2026, 10, 14, 14, 0, tzinfo=UTC)
        assert is_open(WEEKDAYS_8_TO_5, early, "America/New_York") is True
        assert is_open(WEEKDAYS_8_TO_5, early, "America/Los_Angeles") is False

    def test_weekend_is_after_hours(self):
        saturday = datetime(2026, 10, 17, 15, 0, tzinfo=UTC)
        assert is_after_hours(WEEKDAYS_8_TO_5, saturday, "America/New_York") is True

    def test_2am_tuesday_is_after_hours(self):
        two_am_local = datetime(2026, 10, 14, 6, 0, tzinfo=UTC)  # 02:00 EDT
        assert is_after_hours(WEEKDAYS_8_TO_5, two_am_local, "America/New_York") is True

    def test_a_window_that_wraps_midnight_stays_open(self):
        # A towing outfit that works 18:00 to 06:00.
        overnight = BusinessHours(
            tue=DayHours(open=time(18), close=time(6)),
            wed=DayHours(open=time(18), close=time(6)),
        )
        # 03:00 Wednesday local is inside Tuesday's window.
        three_am = datetime(2026, 10, 14, 7, 0, tzinfo=UTC)
        assert is_open(overnight, three_am, "America/New_York") is True

    def test_equal_open_and_close_means_around_the_clock(self):
        always = BusinessHours(wed=DayHours(open=time(0), close=time(0)))
        assert is_open(always, NOW, "America/New_York") is True

    def test_naive_datetime_is_a_bug(self):
        with pytest.raises(ValueError, match="naive datetime"):
            is_open(WEEKDAYS_8_TO_5, datetime(2026, 10, 14, 15, 0), "America/New_York")  # noqa: DTZ001


class TestQuietHours:
    def test_the_1am_to_5am_override_wraps_midnight(self):
        two_am_local = datetime(2026, 10, 14, 6, 0, tzinfo=UTC)
        assert in_quiet_hours(time(1), time(5), two_am_local, "America/New_York") is True

    def test_noon_is_not_quiet(self):
        noon_local = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)
        assert in_quiet_hours(time(1), time(5), noon_local, "America/New_York") is False

    def test_equal_bounds_mean_no_quiet_hours(self):
        assert in_quiet_hours(time(1), time(1), NOW, "America/New_York") is False
