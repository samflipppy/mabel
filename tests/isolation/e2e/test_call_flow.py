"""Capture, emergency → owner SMS, recap, archive. Bot never texts the caller."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from mabel_db.tenant import tenant_scope
from mabel_mcp.registry import dispatch
from mabel_media.postcall import CallOutcome, finalize
from mabel_telnyx.client import FakeTelnyxClient
from mabel_worker.jobs import morning_recap, send_notification
from mabel_worker.queue import Job
from sqlalchemy import text

from tests.e2e.fakes import FakeObjectStore, token_for

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 10, 14, 6, 30, tzinfo=UTC)
CALLER = "+12165550177"
OWNER = "+12165550123"


def _job(tenant_id: UUID, kind: str) -> Job:
    return Job(
        id=1,
        tenant_id=tenant_id,
        kind=kind,
        payload={},
        attempts=0,
        max_attempts=3,
        created_at=NOW,
    )


class TestCapture:
    async def test_create_lead_stores_the_six_fields_and_no_money(
        self, app_engine, two_tenants: tuple[UUID, UUID]
    ):
        alpha, _beta = two_tenants
        result = await dispatch(
            "create_lead",
            {
                "name": "Pat Example",
                "phone": "216-555-0177",
                "address": "100 Example Ave",
                "job_type": "water heater",
                "description": "Leaking all over the floor",
                "urgency": "soon",
                "source": "google",
                "value_cents": 380000,
                "price": "$3800",
            },
            token=token_for(alpha),
            engine=app_engine,
            now=NOW,
        )
        assert result.content["created"] is True

        async with tenant_scope(alpha, engine=app_engine) as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT caller_name, service_address, callback_e164, job_type, "
                        "description, urgency, source, value_cents FROM leads "
                        "WHERE caller_name = 'Pat Example'"
                    )
                )
            ).mappings().one()
        assert row["caller_name"] == "Pat Example"
        assert row["service_address"] == "100 Example Ave"
        assert row["callback_e164"] == CALLER
        assert row["job_type"] == "water heater"
        assert row["description"] == "Leaking all over the floor"
        assert row["urgency"] == "soon"
        assert row["source"] == "google"
        assert row["value_cents"] is None


class TestEmergencyGoesToTheOwner:
    async def test_emergency_queues_owner_sms_not_the_caller(
        self, app_engine, two_tenants: tuple[UUID, UUID]
    ):
        alpha, _beta = two_tenants
        async with tenant_scope(alpha, engine=app_engine) as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (tenant_id, email, full_name, phone_e164, role, "
                    "notify_emergencies) VALUES (:t, 'ray-e2e@example.com', 'Ray', "
                    ":p, 'owner', true)"
                ),
                {"t": alpha, "p": OWNER},
            )

        result = await dispatch(
            "escalate_emergency",
            {
                "name": "Pat",
                "phone": "216-555-0177",
                "address": "100 Example Ave",
                "nature": "burst pipe",
            },
            token=token_for(alpha, "call_emg"),
            engine=app_engine,
            now=NOW,
        )
        assert result.content["escalated"] is True
        assert result.content["oncall_reached"] is True

        async with tenant_scope(alpha, engine=app_engine) as conn:
            queued = (
                await conn.execute(
                    text(
                        "SELECT to_address, body, kind FROM notifications "
                        "WHERE kind = 'emergency'"
                    )
                )
            ).mappings().all()
        assert len(queued) == 1
        assert queued[0]["to_address"] == OWNER
        assert queued[0]["to_address"] != CALLER
        assert "$" not in queued[0]["body"]
        assert "380" not in queued[0]["body"]

    async def test_the_worker_sends_only_to_the_owner_with_a_fake_telnyx(
        self, app_engine, two_tenants: tuple[UUID, UUID], monkeypatch
    ):
        alpha, _beta = two_tenants
        async with tenant_scope(alpha, engine=app_engine) as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (tenant_id, email, phone_e164, role, "
                    "notify_emergencies) VALUES (:t, 'ray-send@example.com', "
                    ":p, 'owner', true)"
                ),
                {"t": alpha, "p": OWNER},
            )
        await dispatch(
            "escalate_emergency",
            {"name": "Pat", "phone": "216-555-0177", "nature": "burst pipe"},
            token=token_for(alpha, "call_send"),
            engine=app_engine,
            now=NOW,
        )
        monkeypatch.setenv("TELNYX_FROM_E164", "+12165550148")
        fake = FakeTelnyxClient()
        await send_notification.run(_job(alpha, "send_notification"), app_engine, client=fake)
        assert [m.to_e164 for m in fake.sent] == [OWNER]
        assert CALLER not in {m.to_e164 for m in fake.sent}


class TestMorningRecap:
    async def test_routine_lead_lands_on_the_7am_recap_not_an_emergency(
        self, app_engine, two_tenants: tuple[UUID, UUID]
    ):
        alpha, _beta = two_tenants
        async with tenant_scope(alpha, engine=app_engine) as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (tenant_id, email, phone_e164, role, "
                    "notify_recap) VALUES (:t, 'ray-recap@example.com', :p, 'owner', true)"
                ),
                {"t": alpha, "p": OWNER},
            )
            await conn.execute(
                text(
                    "INSERT INTO leads (tenant_id, caller_name, job_type, urgency, "
                    "callback_e164) VALUES (:t, 'Pat', 'slow drain', 'routine', :c)"
                ),
                {"t": alpha, "c": CALLER},
            )

        await morning_recap.run(_job(alpha, "morning_recap"), app_engine)

        async with tenant_scope(alpha, engine=app_engine) as conn:
            recaps = (
                await conn.execute(
                    text(
                        "SELECT to_address, body FROM notifications WHERE kind = 'morning_recap'"
                    )
                )
            ).mappings().all()
        assert recaps
        assert all(row["to_address"] == OWNER for row in recaps)
        assert all(row["to_address"] != CALLER for row in recaps)
        assert all("$" not in row["body"] or "value" not in row["body"].lower() for row in recaps)


class TestPostCallArchive:
    async def test_transcript_and_recording_land_in_the_mock_store(
        self, app_engine, two_tenants: tuple[UUID, UUID]
    ):
        alpha, beta = two_tenants
        store = FakeObjectStore()
        call = CallOutcome(
            call_id="call_archive_e2e",
            tenant_id=alpha,
            timezone="America/Chicago",
            trade="plumbing",
            from_e164=CALLER,
            to_e164="+12165550148",
            started_at=NOW,
            ended_at=NOW + timedelta(minutes=3),
            turns=[
                {"role": "assistant", "text": "Thanks for calling Ruiz Plumbing."},
                {"role": "caller", "text": "My water heater is leaking."},
            ],
            recording_bytes=b"ulaw-audio-bytes",
            telephony_cost_cents=3,
        )
        archived = await finalize(call, storage=store, engine=app_engine)
        assert archived.recording_path is not None
        assert archived.transcript_path is not None
        assert store.get(archived.recording_path) == b"ulaw-audio-bytes"
        assert b"water heater" in (store.get(archived.transcript_path) or b"")
        assert isinstance(archived.voice_cost_cents, int)
        assert isinstance(archived.telephony_cost_cents, int)

        async with tenant_scope(alpha, engine=app_engine) as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT recording_path, voice_cost_cents FROM calls "
                        "WHERE xai_call_id = 'call_archive_e2e'"
                    )
                )
            ).mappings().one()
            transcript = (
                await conn.execute(text("SELECT full_text FROM transcripts"))
            ).scalar_one()
        assert row["recording_path"] == archived.recording_path
        assert isinstance(row["voice_cost_cents"], int)
        assert "water heater" in transcript

        async with tenant_scope(beta, engine=app_engine) as conn:
            seen = await conn.execute(
                text("SELECT count(*) FROM calls WHERE xai_call_id = 'call_archive_e2e'")
            )
            assert seen.scalar_one() == 0
