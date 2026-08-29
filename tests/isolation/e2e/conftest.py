"""Fixtures for the DB-backed mocked E2E tests.

These live under tests/isolation/ so they share the scratch Postgres and the
mabel_app pool. Do not point them at Fly or Supabase.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import jwt
import pytest
from sqlalchemy import text

from mabel_db.tenant import tenant_scope
from mabel_media.inbound import bind_inbound_opener

JWT_SECRET = "test-supabase-jwt-secret-not-a-real-key"
MCP_KEY = "a-test-signing-key-long-enough-to-be-accepted"


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
    from tests.isolation.conftest import _test_database_url

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

