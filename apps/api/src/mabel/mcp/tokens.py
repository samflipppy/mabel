"""Short-lived tenant-scoped tokens.

Minted server-side after DID resolution. Tool handlers trust this, not an argument.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from uuid import UUID, uuid4

from mabel.platform.config import ConfigError, require_mcp_token_secret

TOKEN_TTL_SECONDS = 60 * 15


class TokenError(ValueError):
    """This token is not one Mabel minted."""


@dataclass(frozen=True)
class TenantToken:
    tenant_id: UUID
    token_id: str
    expires_at: int


def mint_tenant_token(
    tenant_id: UUID, *, now: int | None = None, ttl: int = TOKEN_TTL_SECONDS
) -> str:
    secret = require_mcp_token_secret()
    issued = int(time.time() if now is None else now)
    payload = {
        "tenant_id": str(tenant_id),
        "jti": uuid4().hex,
        "exp": issued + ttl,
        "iat": issued,
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64(_sign(secret, body))
    return f"{body}.{sig}"


def parse_tenant_token(token: str, *, now: int | None = None) -> TenantToken:
    secret = require_mcp_token_secret()
    try:
        body, sig = token.split(".", 1)
    except ValueError as exc:
        raise TokenError("Mabel does not accept this token.") from exc
    expected = _sign(secret, body)
    given = _unb64(sig)
    if not hmac.compare_digest(given, expected):
        raise TokenError("Mabel does not accept this token.")
    try:
        payload = json.loads(_unb64(body).decode("utf-8"))
        tenant_id = UUID(payload["tenant_id"])
        expires_at = int(payload["exp"])
        token_id = str(payload["jti"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise TokenError("Mabel does not accept this token.") from exc
    current = int(time.time() if now is None else now)
    if expires_at < current:
        raise TokenError("This token has expired.")
    return TenantToken(tenant_id=tenant_id, token_id=token_id, expires_at=expires_at)


def bearer_tenant(authorization: str | None) -> UUID:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise TokenError("Mabel needs a tenant token on this request.")
    token = authorization.split(" ", 1)[1].strip()
    return parse_tenant_token(token).tenant_id


def missing_secret_error() -> ConfigError:
    return ConfigError("Mabel is missing her token secret. Set MABEL_MCP_TOKEN_SECRET.")


def _sign(secret: str, body: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()


def _b64(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    import base64

    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)
