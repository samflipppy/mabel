"""Admin HTTP auth for POST /shops.

onboard_shop() does not read this. The model does not create tenants.
Never log the token.
"""

from __future__ import annotations

import hmac

from mabel.platform.config import require_admin_token

NEED_ADMIN_TOKEN = "Mabel needs an admin token on this request."
BAD_ADMIN_TOKEN = "Mabel does not accept this token."


class AdminAuthError(ValueError):
    """Wrong or missing bearer token. Never include the token in the message."""


def verify_admin_authorization(authorization: str | None) -> None:
    """Fail closed. Missing config is ConfigError. Wrong bearer is AdminAuthError."""
    expected = require_admin_token()
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AdminAuthError(NEED_ADMIN_TOKEN)
    given = authorization.split(" ", 1)[1].strip()
    if not given or not _tokens_match(given, expected):
        raise AdminAuthError(BAD_ADMIN_TOKEN)


def _tokens_match(given: str, expected: str) -> bool:
    given_b = given.encode("utf-8")
    expected_b = expected.encode("utf-8")
    if len(given_b) != len(expected_b):
        return False
    return hmac.compare_digest(given_b, expected_b)
