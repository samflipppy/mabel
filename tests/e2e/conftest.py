"""Shared fixtures for the mocked end-to-end suite.

Reuses the isolation scratch Postgres (TEST_DATABASE_URL). Production-shaped
URLs are refused there, not here. These tests never point at Fly or Supabase.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid4

import jwt
import pytest
from sqlalchemy import text

from mabel_db.tenant import tenant_scope
from mabel_media.inbound import bind_inbound_opener
from tests.isolation.conftest import SKIP_REASON, _test_database_url

pytest_plugins = ["tests.isolation.conftest"]

HERE = Path(__file__).parent

# Files that do not need a database. Everything else is skipped when the
# scratch URL is unset, same loud reason as the isolation suite.
NEEDS_NO_DATABASE = {
    "test_missing_secrets.py",
    "test_collect_and_guardrails.py",
    "test_golden_verticals.py",
}

JWT_SECRET = "test-supabase-jwt-secret-not-a-real-key"
MCP_KEY = "a-test-signing-key-long-enough-to-be-accepted"
XAI_WEBHOOK_SECRET = "whsec_dGVzdC1zaWduaW5nLXNlY3JldC1ub3QtcmVhbA"


def pytest_collection_modifyitems(config, items):
    if _test_database_url() is not None:
        return
    skip = pytest.mark.skip(reason=SKIP_REASON)
    for item in items:
        path = Path(str(item.fspath))
        if HERE in path.parents and path.name not in NEEDS_NO_DATABASE:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _reset_inbound_opener():
    bind_inbound_opener(None)
    yield
    bind_inbound_opener(None)


@pytest.fixture
def portal_secret(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("MCP_TOKEN_SIGNING_KEY", MCP_KEY)
    return JWT_SECRET


@pytest.fixture
def bind_db(app_engine, monkeypatch):
    """Portal and webhook routes call get_engine(). Point them at the scratch
    pool that already runs as mabel_app, so RLS is actually on."""
    url = _test_database_url()
    assert url is not None
    monkeypatch.setenv("DATABASE_URL", url)

    from mabel_db import tenant as tenant_mod

    monkeypatch.setattr(tenant_mod, "get_engine", lambda: app_engine)
    return app_engine


def mint_portal_jwt(supabase_uid: UUID, *, secret: str = JWT_SECRET) -> str:
    from datetime import UTC, datetime, timedelta

    return jwt.encode(
        {
            "sub": str(supabase_uid),
            "aud": "authenticated",
            "exp": datetime.now(UTC) + timedelta(hours=1),
            "role": "authenticated",
        },
        secret,
        algorithm="HS256",
    )


@pytest.fixture
async def portal_owners(app_engine, two_tenants: tuple[UUID, UUID], portal_secret, bind_db):
    """An owner on each tenant, with a Supabase uid the portal JWT can name."""
    del portal_secret, bind_db
    alpha, beta = two_tenants
    alpha_uid, beta_uid = uuid4(), uuid4()
    owners = {}
    for tenant_id, uid, email, phone in (
        (alpha, alpha_uid, "ray@ruiz.example", "+12165550111"),
        (beta, beta_uid, "dee@delgado.example", "+12165550222"),
    ):
        async with tenant_scope(tenant_id, engine=app_engine) as conn:
            result = await conn.execute(
                text(
                    "INSERT INTO users (tenant_id, supabase_uid, email, full_name, "
                    "phone_e164, role, notify_emergencies, notify_recap) "
                    "VALUES (:t, :uid, :e, 'Owner', :p, 'owner', true, true) "
                    "RETURNING id"
                ),
                {"t": tenant_id, "uid": uid, "e": email, "p": phone},
            )
            user_id = result.scalar_one()
        owners[tenant_id] = {
            "user_id": user_id,
            "supabase_uid": uid,
            "email": email,
            "phone": phone,
            "token": mint_portal_jwt(uid),
        }
    return {"alpha": alpha, "beta": beta, "owners": owners}


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
