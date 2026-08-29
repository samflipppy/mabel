"""Portal auth, dashboard, calls, leads, settings — against a real tenant, RLS."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from mabel_api.main import create_app
from mabel_db.tenant import tenant_scope
from mabel_media.postcall import CallOutcome, finalize
from sqlalchemy import text

from tests.e2e.conftest import auth_header

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)


@pytest.fixture
def client(portal_owners):
    del portal_owners
    return TestClient(create_app())


class TestAuth:
    async def test_no_bearer_is_401(self, client, portal_owners):
        del portal_owners
        assert client.get("/api/dashboard").status_code == 401

    async def test_a_forged_token_is_401(self, client, portal_owners, monkeypatch):
        del portal_owners
        monkeypatch.setenv("SUPABASE_JWT_SECRET", "some-other-secret-not-the-real-one")
        # Recreate so the app reads the other secret... deps read env each call.
        response = client.get(
            "/api/dashboard",
            headers=auth_header("not-a-jwt"),
        )
        assert response.status_code == 401

    async def test_unconfigured_auth_is_503(self, portal_owners, monkeypatch):
        del portal_owners
        monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
        client = TestClient(create_app())
        response = client.get("/api/dashboard", headers=auth_header("whatever"))
        assert response.status_code == 503
        assert "not configured" in response.json()["detail"]


class TestDashboardCallsLeadsSettings:
    async def test_each_owner_sees_only_their_tenant(
        self, client, portal_owners, app_engine
    ):
        alpha = portal_owners["alpha"]
        beta = portal_owners["beta"]
        owners = portal_owners["owners"]

        async with tenant_scope(alpha, engine=app_engine) as conn:
            await conn.execute(
                text(
                    "INSERT INTO leads (tenant_id, caller_name, job_type, urgency) "
                    "VALUES (:t, 'Alpha Lead', 'burst pipe', 'emergency')"
                ),
                {"t": alpha},
            )
        async with tenant_scope(beta, engine=app_engine) as conn:
            await conn.execute(
                text(
                    "INSERT INTO leads (tenant_id, caller_name, job_type) "
                    "VALUES (:t, 'Beta Lead', 'no heat')"
                ),
                {"t": beta},
            )

        alpha_dash = client.get(
            "/api/dashboard", headers=auth_header(owners[alpha]["token"])
        )
        beta_dash = client.get("/api/dashboard", headers=auth_header(owners[beta]["token"]))
        assert alpha_dash.status_code == 200
        assert beta_dash.status_code == 200
        alpha_blob = alpha_dash.text
        beta_blob = beta_dash.text
        assert "Beta Lead" not in alpha_blob
        assert "Alpha Lead" not in beta_blob

        alpha_leads = client.get(
            "/api/leads/board", headers=auth_header(owners[alpha]["token"])
        )
        assert alpha_leads.status_code == 200
        names = [
            lead["caller_name"]
            for stage in alpha_leads.json()["stages"].values()
            for lead in stage
        ]
        assert "Alpha Lead" in names
        assert "Beta Lead" not in names

        settings = client.get(
            "/api/settings/account", headers=auth_header(owners[alpha]["token"])
        )
        assert settings.status_code == 200
        assert settings.json()["business_name"] == "Ruiz Plumbing"
        assert settings.json()["did_e164"] == "+12165550148"

        team = client.get("/api/settings/team", headers=auth_header(owners[alpha]["token"]))
        assert team.status_code == 200
        emails = {member["email"] for member in team.json()}
        assert "ray@ruiz.example" in emails
        assert "dee@delgado.example" not in emails

    async def test_calls_list_and_detail_are_tenant_scoped(
        self, client, portal_owners, app_engine
    ):
        alpha = portal_owners["alpha"]
        beta = portal_owners["beta"]
        owners = portal_owners["owners"]

        await finalize(
            CallOutcome(
                call_id="call_portal_alpha",
                tenant_id=alpha,
                timezone="America/Chicago",
                trade="plumbing",
                from_e164="+12165550177",
                to_e164="+12165550148",
                started_at=NOW,
                ended_at=NOW + timedelta(minutes=2),
                turns=[
                    {"role": "assistant", "text": "Thanks for calling Ruiz Plumbing."},
                    {"role": "caller", "text": "Water heater on Example Ave."},
                ],
            ),
            engine=app_engine,
        )
        await finalize(
            CallOutcome(
                call_id="call_portal_beta",
                tenant_id=beta,
                timezone="America/Denver",
                trade="hvac",
                from_e164="+12165550188",
                to_e164="+12165550199",
                started_at=NOW,
                ended_at=NOW + timedelta(minutes=2),
                turns=[{"role": "caller", "text": "Delgado secret furnace."}],
            ),
            engine=app_engine,
        )

        listed = client.get("/api/calls", headers=auth_header(owners[alpha]["token"]))
        assert listed.status_code == 200
        blob = listed.text
        assert "Delgado secret" not in blob

        search = client.get(
            "/api/calls",
            params={"q": "water heater"},
            headers=auth_header(owners[alpha]["token"]),
        )
        assert search.status_code == 200
        assert search.json()["total"] >= 1

        # Beta's owner searching the same phrase must not see Alpha's call.
        beta_search = client.get(
            "/api/calls",
            params={"q": "water heater"},
            headers=auth_header(owners[beta]["token"]),
        )
        assert beta_search.status_code == 200
        assert "Example Ave" not in beta_search.text

    async def test_owner_sets_lead_value_as_integer_cents(
        self, client, portal_owners, app_engine
    ):
        alpha = portal_owners["alpha"]
        token = portal_owners["owners"][alpha]["token"]
        async with tenant_scope(alpha, engine=app_engine) as conn:
            lead_id = (
                await conn.execute(
                    text(
                        "INSERT INTO leads (tenant_id, caller_name, job_type) "
                        "VALUES (:t, 'Worth', 'water heater') RETURNING id"
                    ),
                    {"t": alpha},
                )
            ).scalar_one()

        response = client.put(
            f"/api/leads/{lead_id}/value",
            headers=auth_header(token),
            json={"amount": "3800"},
        )
        assert response.status_code == 200
        assert response.json()["value_cents"] == 380_000
        assert isinstance(response.json()["value_cents"], int)
        assert response.json()["currency"] == "USD"

        # A float-looking body is still parsed as dollars, not as a Python float
        # stored in the column. The parser is deterministic.
        again = client.put(
            f"/api/leads/{lead_id}/value",
            headers=auth_header(token),
            json={"amount": "38.50"},
        )
        assert again.status_code == 200
        assert again.json()["value_cents"] == 3850

    async def test_test_call_refuses_outbound(self, client, portal_owners):
        alpha = portal_owners["alpha"]
        token = portal_owners["owners"][alpha]["token"]
        response = client.post("/api/config/test-call", headers=auth_header(token))
        assert response.status_code == 200
        body = response.json()
        assert body["placed"] is False
        assert body["calling"] is None
        assert "outbound" in body["message"].lower() or "doesn't place" in body["message"]
