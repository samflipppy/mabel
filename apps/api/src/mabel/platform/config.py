from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """Missing config. Never include the secret in the message."""


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    mcp_token_secret: str | None
    xai_webhook_secret: str | None
    xai_api_key_present: bool
    telnyx_api_key_present: bool


def load_settings() -> Settings:
    return Settings(
        database_url=_optional("DATABASE_URL"),
        mcp_token_secret=_optional("MABEL_MCP_TOKEN_SECRET"),
        xai_webhook_secret=_optional("XAI_WEBHOOK_SECRET"),
        xai_api_key_present=bool(_optional("XAI_API_KEY")),
        telnyx_api_key_present=bool(_optional("TELNYX_API_KEY")),
    )


def require_mcp_token_secret() -> str:
    secret = _optional("MABEL_MCP_TOKEN_SECRET")
    if not secret:
        raise ConfigError("Mabel is missing her token secret. Set MABEL_MCP_TOKEN_SECRET.")
    return secret


def require_webhook_secret() -> str:
    secret = _optional("XAI_WEBHOOK_SECRET")
    if not secret:
        raise ConfigError(
            "Mabel cannot verify this call. Webhook signing is not configured."
        )
    return secret


def xai_ready() -> bool:
    return bool(_optional("XAI_API_KEY"))


def telnyx_ready() -> bool:
    return bool(_optional("TELNYX_API_KEY"))


def _optional(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
