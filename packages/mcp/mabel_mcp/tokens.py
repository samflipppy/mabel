"""The call token. The whole of Mabel's tenant security rests on this.

Invariant 3 and invariant 5 of AGENTS.md, in one place: the tenant is resolved
server-side from the dialed number, before the socket opens, and the MCP server
trusts the token that resolution minted — never a tool argument.

The shape of the trust:

1. `realtime.call.incoming` arrives. We verify its signature.
2. We look up `to` in `tenants.did_e164`. **That** is the tenant. Nothing the
   model says influenced it.
3. We mint a token carrying that `tenant_id` and this `call_id`, 15-minute TTL.
4. The token goes into `session.update` as the MCP `authorization` header.
5. Every tool handler reads the tenant from the token and calls
   `tenant_scope(tenant_id)`. No handler takes a tenant identifier as an
   argument, and `assert_no_tenant_argument` makes that structural rather than
   a convention.

The model can say anything it likes. It cannot say which tenant's data it sees.

Fifteen minutes, and a 120-minute maximum session, means a long call outlives
its token. `remaining_seconds` exists so the media process can mint a fresh one
mid-call rather than discovering the expiry when a tool fails at minute
sixteen.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from uuid import UUID

import jwt

ALGORITHM = "HS256"
ISSUER = "mabel"
AUDIENCE = "mabel-mcp"

# 03-VOICE.md. Short enough that a leaked token is nearly worthless, long
# enough to cover an ordinary call without a refresh.
TTL_SECONDS = 15 * 60

# Clock skew between the minting process and the serving one.
LEEWAY_SECONDS = 30

# Refresh when this little is left, so a tool call never lands on an expiry.
REFRESH_BELOW_SECONDS = 120


class TokenError(Exception):
    """The token is missing, malformed, expired, or not ours."""


class SigningKeyUnavailable(TokenError):
    """No signing key. We fail closed: with no key we mint nothing and trust
    nothing. See docs/BLOCKED.md #1."""


def signing_key() -> str:
    key = os.environ.get("MCP_TOKEN_SIGNING_KEY")
    if not key:
        raise SigningKeyUnavailable(
            "MCP_TOKEN_SIGNING_KEY is unset. Without it we cannot prove which "
            "tenant a tool call belongs to, so we mint nothing and trust nothing. "
            "See docs/BLOCKED.md #1."
        )
    if len(key) < 32:
        # A short HMAC key is brute-forceable, and what it protects here is
        # cross-tenant access to call data.
        raise SigningKeyUnavailable("MCP_TOKEN_SIGNING_KEY must be at least 32 characters")
    return key


@dataclass(frozen=True, slots=True)
class CallToken:
    """What a verified token proves: this call, this tenant, until this time."""

    tenant_id: UUID
    call_id: str
    expires_at: int
    issued_at: int

    def remaining_seconds(self, *, now: float | None = None) -> int:
        return int(self.expires_at - (time.time() if now is None else now))

    def needs_refresh(self, *, now: float | None = None) -> bool:
        return self.remaining_seconds(now=now) < REFRESH_BELOW_SECONDS


def mint_call_token(
    tenant_id: UUID | str,
    call_id: str,
    *,
    key: str | None = None,
    ttl_seconds: int = TTL_SECONDS,
    now: float | None = None,
) -> str:
    """Mint a token after the DID resolved to a tenant. Never before.

    `tenant_id` is validated as a UUID here for the same reason `tenant_scope`
    validates it: this value ends up deciding which rows a handler can see.
    """
    try:
        scoped = tenant_id if isinstance(tenant_id, UUID) else UUID(str(tenant_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise TokenError(f"tenant_id must be a UUID, got {tenant_id!r}") from exc

    if not call_id or not isinstance(call_id, str):
        raise TokenError(f"call_id must be a non-empty string, got {call_id!r}")

    issued = int(time.time() if now is None else now)
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": str(scoped),
        "tenant_id": str(scoped),
        "call_id": call_id,
        "iat": issued,
        "nbf": issued,
        "exp": issued + ttl_seconds,
    }
    return jwt.encode(payload, key or signing_key(), algorithm=ALGORITHM)


def verify_call_token(token: str, *, key: str | None = None, now: float | None = None) -> CallToken:
    """Verify, or raise. The returned tenant is the only tenant this request
    may touch."""
    if not token:
        raise TokenError("no token presented")

    # PyJWT reads the wall clock and offers no way to inject one, so when a
    # caller supplies `now` we turn its time checks off and do them below
    # against that value. Signature, audience and issuer are still PyJWT's
    # job — those are the ones that must never be reimplemented by hand.
    options: dict[str, object] = {
        "require": ["exp", "iat", "aud", "iss", "tenant_id", "call_id"],
    }
    if now is not None:
        options |= {"verify_exp": False, "verify_nbf": False, "verify_iat": False}

    try:
        claims = jwt.decode(
            token,
            key or signing_key(),
            # A list of exactly one. Passing the algorithm explicitly is what
            # stops the `alg: none` and the RS256-key-confusion attacks, both
            # of which would let anyone mint a token for any tenant.
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            leeway=LEEWAY_SECONDS,
            options=options,
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token has expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise TokenError("token was not minted for the MCP server") from exc
    except jwt.InvalidIssuerError as exc:
        raise TokenError("token was not minted by us") from exc
    except jwt.MissingRequiredClaimError as exc:
        raise TokenError(f"token is missing a required claim: {exc}") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"token is not valid: {exc}") from exc

    try:
        tenant_id = UUID(claims["tenant_id"])
    except (ValueError, TypeError, KeyError) as exc:
        raise TokenError("token carries no usable tenant_id") from exc

    expires_at = int(claims["exp"])
    issued_at = int(claims["iat"])
    if now is not None:
        if expires_at + LEEWAY_SECONDS < now:
            raise TokenError("token has expired")
        if issued_at - LEEWAY_SECONDS > now:
            raise TokenError("token was issued in the future")

    return CallToken(
        tenant_id=tenant_id,
        call_id=str(claims["call_id"]),
        expires_at=expires_at,
        issued_at=issued_at,
    )


def bearer(token: str) -> str:
    """The `authorization` value on the MCP tool entry in `session.update`."""
    return f"Bearer {token}"


def from_authorization_header(header: str | None) -> str:
    """Pull the token out of an `Authorization: Bearer ...` header."""
    if not header:
        raise TokenError("no Authorization header")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise TokenError("Authorization header is not a Bearer token")
    return value.strip()
