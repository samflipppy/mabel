"""Mabel's MCP server.

Nine tools, Streamable HTTP, and one rule that everything else follows from:
the tenant comes from the call token, which was minted from the dialed number
before the session opened. Never from a tool argument.
"""

from __future__ import annotations

from mabel_mcp.schemas import (
    BY_NAME,
    FORBIDDEN_ARGUMENT_NAMES,
    TOOL_NAMES,
    TOOLS,
    SchemaViolation,
    ToolSchema,
    assert_no_tenant_argument,
)
from mabel_mcp.tokens import (
    REFRESH_BELOW_SECONDS,
    TTL_SECONDS,
    CallToken,
    SigningKeyUnavailable,
    TokenError,
    bearer,
    from_authorization_header,
    mint_call_token,
    verify_call_token,
)

__all__ = [
    "BY_NAME",
    "FORBIDDEN_ARGUMENT_NAMES",
    "REFRESH_BELOW_SECONDS",
    "TOOLS",
    "TOOL_NAMES",
    "TTL_SECONDS",
    "CallToken",
    "SchemaViolation",
    "SigningKeyUnavailable",
    "ToolSchema",
    "TokenError",
    "assert_no_tenant_argument",
    "bearer",
    "from_authorization_header",
    "mint_call_token",
    "verify_call_token",
]
