"""Collect the six fields. Never quote a price, take payment, or invent
an arrival. The test-call button refuses outbound.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from mabel_api.main import create_app
from mabel_media.prompt import PromptInputs, render_prompt
from mabel_media.qa import QaInputs, review
from mabel_verticals.loader import load_latest
from mabel_xai.client import FORBIDDEN_MODEL_ALIAS, VOICE_MODEL


def _prompt() -> str:
    return render_prompt(
        PromptInputs(
            business_name="Ruiz Plumbing",
            trade="plumbing",
            city="Lakewood",
            greeting="Thanks for calling Ruiz Plumbing.",
            services=["drain cleaning", "water heaters"],
            services_declined=["septic tanks"],
            service_area_zips=["44107", "44116"],
            service_area_note=None,
            knowledge=[],
            never_say=["price", "estimate_range", "hourly_rate", "arrival_time"],
            custom_rules=None,
            ruleset=load_latest("plumbing"),
            emergency_overrides={},
        )
    )


def test_the_prompt_asks_for_the_six_fields():
    prompt = _prompt()
    assert "Their name" in prompt
    assert "service address" in prompt.lower()
    assert "callback number" in prompt.lower()
    assert "What they need" in prompt
    assert "urgent" in prompt.lower()
    assert "heard about" in prompt.lower()


def test_the_prompt_forbids_price_payment_and_invented_arrival():
    prompt = _prompt()
    assert "Never state a price" in prompt
    assert "payment" in prompt.lower() or "card" in prompt.lower() or "bank" in prompt.lower()
    assert "Never promise an arrival time" in prompt
    assert VOICE_MODEL != FORBIDDEN_MODEL_ALIAS
    assert "grok-voice-latest" not in prompt


def test_qa_flags_a_quoted_price():
    flags = review(
        QaInputs(
            duration_sec=90,
            started_at=datetime(2026, 10, 14, 18, 0, tzinfo=UTC),
            timezone="America/Chicago",
            assistant_text="That'll be about 380 dollars.",
            backstop_escalates=False,
            escalated=False,
            booked_a_slot=False,
        )
    )
    assert "quoted_price" in flags


def test_qa_flags_an_invented_arrival():
    flags = review(
        QaInputs(
            duration_sec=90,
            started_at=datetime(2026, 10, 14, 18, 0, tzinfo=UTC),
            timezone="America/Chicago",
            assistant_text="Someone will be there at 3:30 pm.",
            backstop_escalates=False,
            escalated=False,
            booked_a_slot=False,
        )
    )
    assert "promised_arrival" in flags


def test_qa_does_not_flag_an_arrival_that_came_from_the_calendar():
    flags = review(
        QaInputs(
            duration_sec=90,
            started_at=datetime(2026, 10, 14, 18, 0, tzinfo=UTC),
            timezone="America/Chicago",
            assistant_text="I have Thursday at 3:30, does that work?",
            backstop_escalates=False,
            escalated=False,
            booked_a_slot=True,
        )
    )
    assert "promised_arrival" not in flags


def test_test_call_is_mounted_and_refuses_without_forging_a_session(monkeypatch):
    """The button stays. The endpoint refuses. Unauthenticated is still a refuse
    of outbound — 401, not a placed call."""
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    client = TestClient(create_app())
    response = client.post("/api/config/test-call")
    assert response.status_code in {401, 503}
    if response.status_code == 200:
        raise AssertionError("test-call must not succeed without a session")
    blob = response.text.lower()
    assert "placed" not in blob or "true" not in blob
