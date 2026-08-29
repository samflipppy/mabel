"""Owner SMS intents over the inbound Telnyx webhook.

Signature on the raw body. Idempotent on webhook-id. Stale >300s refused.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from mabel_db.tenant import tenant_scope
from sqlalchemy import text

from tests.e2e.fakes import sign_telnyx, telnyx_keypair, telnyx_sms_body

pytestmark = pytest.mark.asyncio

ALPHA_PHONE = "+12165550111"
BETA_PHONE = "+12165550222"


@pytest.fixture
async def owners(app_engine, two_tenants: tuple[UUID, UUID], bind_db):
    alpha, beta = two_tenants
    for tenant_id, phone, email in (
        (alpha, ALPHA_PHONE, "ray-sms@ruiz.example"),
        (beta, BETA_PHONE, "dee-sms@delgado.example"),
    ):
        async with tenant_scope(tenant_id, engine=app_engine) as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (tenant_id, email, full_name, phone_e164, role, "
                    "notify_recap, notify_emergencies) "
                    "VALUES (:t, :e, 'Owner', :p, 'owner', true, true)"
                ),
                {"t": tenant_id, "e": email, "p": phone},
            )
            await conn.execute(
                text(
                    "INSERT INTO leads (tenant_id, caller_name, job_type, urgency, status) "
                    "VALUES (:t, 'Henderson', 'water heater', 'routine', 'new')"
                ),
                {"t": tenant_id},
            )
    return alpha, beta


@pytest.fixture
def telnyx_env(monkeypatch, bind_db):
    key, public = telnyx_keypair()
    monkeypatch.setenv("TELNYX_PUBLIC_KEY", public)
    return key, bind_db


class TestSignatureAndIdempotency:
    async def test_unsigned_is_401(self, client, telnyx_env, owners):
        del owners
        _key, _engine = telnyx_env
        body = telnyx_sms_body(event_id="evt_unsigned", from_number=ALPHA_PHONE, text="FU")
        response = await client.post("/webhooks/telnyx/sms", content=body)
        assert response.status_code == 401

    async def test_stale_is_401(self, client, telnyx_env, owners):
        del owners
        key, _engine = telnyx_env
        body = telnyx_sms_body(event_id="evt_stale", from_number=ALPHA_PHONE, text="FU")
        headers = sign_telnyx(body, key, at=1_800_000_000.0 - 400)
        response = await client.post("/webhooks/telnyx/sms", content=body, headers=headers)
        assert response.status_code == 401

    async def test_the_same_webhook_id_is_not_acted_on_twice(self, client, telnyx_env, owners):
        alpha, _beta = owners
        key, engine = telnyx_env
        body = telnyx_sms_body(
            event_id="evt_won_once", from_number=ALPHA_PHONE, text="WON HENDERSON 3800"
        )
        headers = sign_telnyx(body, key)
        first = await client.post("/webhooks/telnyx/sms", content=body, headers=headers)
        second = await client.post("/webhooks/telnyx/sms", content=body, headers=headers)
        assert first.status_code == 200
        assert first.json()["action"] == "marked_won"
        assert second.status_code == 200
        assert second.json()["status"] == "duplicate"

        async with tenant_scope(alpha, engine=engine) as conn:
            count = await conn.execute(text("SELECT count(*) FROM leads WHERE status = 'won'"))
            assert count.scalar_one() == 1


class TestIntentsStayInTheirTenant:
    async def test_won_writes_integer_cents_on_the_senders_lead_only(
        self, client, telnyx_env, owners
    ):
        alpha, beta = owners
        key, engine = telnyx_env
        body = telnyx_sms_body(
            event_id="evt_won", from_number=ALPHA_PHONE, text="WON HENDERSON 3800"
        )
        response = await client.post(
            "/webhooks/telnyx/sms", content=body, headers=sign_telnyx(body, key)
        )
        assert response.status_code == 200
        assert response.json()["action"] == "marked_won"

        async with tenant_scope(alpha, engine=engine) as conn:
            mine = (
                await conn.execute(
                    text("SELECT status, value_cents FROM leads WHERE caller_name = 'Henderson'")
                )
            ).mappings().one()
        assert mine["status"] == "won"
        assert isinstance(mine["value_cents"], int)
        assert mine["value_cents"] == 380_000

        async with tenant_scope(beta, engine=engine) as conn:
            theirs = (
                await conn.execute(
                    text("SELECT status, value_cents FROM leads WHERE caller_name = 'Henderson'")
                )
            ).mappings().one()
        assert theirs["status"] == "new"
        assert theirs["value_cents"] is None

    async def test_lost_records_the_reason(self, client, telnyx_env, owners):
        alpha, _beta = owners
        key, engine = telnyx_env
        body = telnyx_sms_body(
            event_id="evt_lost",
            from_number=ALPHA_PHONE,
            text="LOST HENDERSON went with someone else",
        )
        response = await client.post(
            "/webhooks/telnyx/sms", content=body, headers=sign_telnyx(body, key)
        )
        assert response.status_code == 200
        assert response.json()["action"] == "marked_lost"

        async with tenant_scope(alpha, engine=engine) as conn:
            row = (
                await conn.execute(
                    text("SELECT status, lost_reason FROM leads WHERE caller_name = 'Henderson'")
                )
            ).mappings().one()
        assert row["status"] == "lost"
        assert "someone else" in row["lost_reason"]

    async def test_recall_does_not_invent_a_dollar_figure(self, client, telnyx_env, owners):
        alpha, _beta = owners
        key, engine = telnyx_env
        async with tenant_scope(alpha, engine=engine) as conn:
            await conn.execute(
                text(
                    "INSERT INTO communication_events "
                    "(tenant_id, kind, direction, occurred_at, body) "
                    "VALUES (:t, 'note', 'inbound', now(), "
                    "'Caller asked about a water heater replacement')"
                ),
                {"t": alpha},
            )
        body = telnyx_sms_body(
            event_id="evt_recall",
            from_number=ALPHA_PHONE,
            text="anything about a water heater",
        )
        response = await client.post(
            "/webhooks/telnyx/sms", content=body, headers=sign_telnyx(body, key)
        )
        assert response.status_code == 200
        # The reply is queued, not returned as the SMS body. The action is recall.
        assert "$" not in response.text

    async def test_unknown_sender_is_ignored(self, client, telnyx_env, owners):
        del owners
        key, _engine = telnyx_env
        body = telnyx_sms_body(
            event_id="evt_stranger", from_number="+12165559999", text="WON HENDERSON 3800"
        )
        response = await client.post(
            "/webhooks/telnyx/sms", content=body, headers=sign_telnyx(body, key)
        )
        assert response.status_code == 200
        assert response.json()["reason"] == "unknown sender"
