"""asyncpg cannot infer bind types inside CASE, coalesce, or ARRAY[].

SQLAlchemy's text() also declines to bind `:name` when it is immediately
followed by `::`, so `:param::type` is emitted literally and Postgres
rejects it. The fix is `CAST(:param AS type)` / `cast(:param as type)`.

These checks read the query source. They do not need a database, and they
do not walk the call path — that suite already lives in tests/e2e/.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _read(*parts: str) -> str:
    return (REPO.joinpath(*parts)).read_text(encoding="utf-8")


def test_contact_touch_casts_the_optional_now():
    source = _read("packages", "db", "mabel_db", "queries", "contacts.py")
    assert "coalesce(CAST(:now AS timestamptz), now())" in source
    # Insert already used CAST from the mocked E2E work. Keep that style.
    assert "CAST(:phone AS text)" in source


def test_event_append_casts_occurred_at_inside_coalesce():
    source = _read("packages", "db", "mabel_db", "queries", "events.py")
    assert "coalesce(cast(:occurred_at as timestamptz), now())" in source


def test_lead_status_casts_binds_inside_case_and_coalesce():
    source = _read("packages", "db", "mabel_db", "queries", "leads.py")
    assert "cast(:status as text)" in source
    assert "cast(:lost_reason as text)" in source
    assert "cast(:now as timestamptz)" in source
    assert "CASE WHEN :status =" not in source
    assert "coalesce(:now, now())" not in source


def test_queue_enqueue_casts_run_after_inside_coalesce():
    source = _read("apps", "worker", "src", "mabel_worker", "queue.py")
    assert "coalesce(cast(:run_after as timestamptz), now())" in source


def test_sms_won_and_lost_cast_timestamptz_and_cents():
    source = _read("apps", "api", "src", "mabel_api", "sms_router.py")
    assert "CAST(:now AS timestamptz)" in source
    assert "CAST(:value AS bigint)" in source
    assert "coalesce(:now," not in source
    assert "coalesce(:value," not in source


def test_portal_lead_status_casts_inside_case():
    source = _read("apps", "api", "src", "mabel_api", "routes", "leads.py")
    assert "CAST(:status AS text)" in source
    assert "CAST(:reason AS text)" in source
    assert "CASE WHEN :status =" not in source


def test_onboarding_and_settings_cast_optional_text_inside_coalesce():
    onboarding = _read("apps", "api", "src", "mabel_api", "routes", "onboarding.py")
    settings = _read("apps", "api", "src", "mabel_api", "routes", "settings.py")
    assert "coalesce(CAST(:name AS text), full_name)" in onboarding
    assert "coalesce(CAST(:phone AS text), phone_e164)" in settings
