"""Authentication and tenant context for the portal API.

The same principle as the MCP server, arrived at from the other direction: the
tenant is resolved server-side from something the client cannot forge, and
every query runs inside `tenant_scope()` for that tenant.

For the voice path that something is the dialed number. Here it is a Supabase
Auth JWT. In neither case does a request parameter get a say.

**No route takes a tenant identifier.** Not in the path, not in the query
string, not in the body. `tests/property/` enforces that against the OpenAPI
schema, so a route cannot quietly introduce one. If a route needs to act on
another tenant, it is an internal route and it does not exist yet.

Supabase Auth is not configured (docs/BLOCKED.md #1), so `verify_supabase_jwt`
fails closed. There is no development bypass and no `?tenant_id=` escape
hatch — those are the two things that reliably survive into production.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Header, HTTPException, status
from mabel_db.tenant import admin_scope, tenant_scope
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)

# Supabase signs its access tokens with the project JWT secret (HS256) and sets
# `aud` to "authenticated".
SUPABASE_AUDIENCE = "authenticated"
ALGORITHM = "HS256"
LEEWAY_SECONDS = 30


class AuthUnavailable(RuntimeError):
    """No JWT secret configured. See docs/BLOCKED.md #1."""


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Who is asking, and which tenant they belong to.

    `tenant_id` came from our `users` table keyed on the Supabase uid, not from
    a claim the client could have set.
    """

    user_id: UUID
    tenant_id: UUID
    supabase_uid: UUID
    email: str
    role: str

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    @property
    def can_edit_config(self) -> bool:
        # A tech can see the schedule. Changing what Mabel says to customers is
        # the owner's or the office manager's call.
        return self.role in {"owner", "office"}


def jwt_secret() -> str:
    secret = os.environ.get("SUPABASE_JWT_SECRET")
    if not secret:
        raise AuthUnavailable(
            "SUPABASE_JWT_SECRET is unset. The portal API cannot verify who is "
            "asking, so it refuses every request rather than serving one. "
            "See docs/BLOCKED.md #1."
        )
    return secret


def verify_supabase_jwt(token: str, *, secret: str | None = None) -> dict[str, object]:
    """Verify a Supabase access token, or raise.

    The explicit single-algorithm list is what stops `alg: none` and key
    confusion, exactly as in the MCP token verifier. Same reasoning, and worth
    repeating rather than sharing: these are two different trust domains and
    coupling them means a change for one silently applies to the other.
    """
    try:
        return jwt.decode(
            token,
            secret or jwt_secret(),
            algorithms=[ALGORITHM],
            audience=SUPABASE_AUDIENCE,
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="session expired"
        ) from exc
    except jwt.InvalidTokenError as exc:
        # No detail about *why*. A caller learning whether a token was expired
        # or forged is a caller learning something.
        logger.info("rejected a portal token: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated"
        ) from exc


async def current_user(
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    """The FastAPI dependency every portal route uses.

    Two steps, and the second is the important one: the JWT proves *who* they
    are, and our own `users` row says which tenant that is. A `tenant_id` claim
    in the token would be a claim the client's own auth provider set.
    """
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")

    try:
        claims = verify_supabase_jwt(token.strip())
    except AuthUnavailable as exc:
        # Fail closed, and say so as a server error rather than a 401: the
        # request was not wrong, we are not configured.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication is not configured",
        ) from exc

    try:
        supabase_uid = UUID(str(claims["sub"]))
    except (ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated"
        ) from exc

    resolved = await resolve_user(supabase_uid)
    if resolved is None:
        # Authenticated with Supabase but not a Mabel user. Happens when
        # somebody signs up before being invited, and it is a 403 rather than a
        # 401: their credentials are fine, they just are not anybody here.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="no account on this portal"
        )
    return resolved


async def resolve_user(supabase_uid: UUID) -> CurrentUser | None:
    """Map a Supabase uid to one of our users.

    Runs through `admin_scope` because `users` is RLS-protected and there is no
    tenant context yet — the same shape as DID and SMS-sender resolution, and
    the third time it has come up. See migration 0005.
    """
    async with admin_scope(reason="resolve a portal session", engine=None) as conn:
        result = await conn.execute(
            text("SELECT user_id, tenant_id, email, role FROM resolve_user_by_supabase_uid(:uid)"),
            {"uid": supabase_uid},
        )
        row = result.mappings().one_or_none()

    if row is None:
        return None
    return CurrentUser(
        user_id=row["user_id"],
        tenant_id=row["tenant_id"],
        supabase_uid=supabase_uid,
        email=row["email"],
        role=row["role"],
    )


async def tenant_conn(
    user: Annotated[CurrentUser, Depends(current_user)],
) -> AsyncIterator[AsyncConnection]:
    """A connection already scoped to the caller's tenant.

    Routes depend on this rather than opening their own scope, so there is no
    route that *could* forget. A route that takes `tenant_conn` cannot read
    another tenant's rows, and a route that does not take it cannot read
    anything.
    """
    async with tenant_scope(user.tenant_id) as conn:
        yield conn


def require_role(*roles: str):
    """Guard a route behind a role.

    Used sparingly. Most of the portal is visible to anybody in the business;
    what is guarded is publishing a config, billing, and deleting things.
    """

    async def guard(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"this needs one of: {', '.join(sorted(roles))}",
            )
        return user

    return guard


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]
TenantConn = Annotated[AsyncConnection, Depends(tenant_conn)]
