"""The MCP server. Streamable HTTP, because xAI does not support stdio.

Mounted at `/mcp` on the `web` process. xAI connects to it with the
`authorization` header we put in `session.update`, which is the short-lived
token minted from the dialed number.

JSON-RPC 2.0 over POST. Three methods matter: `initialize`, `tools/list`, and
`tools/call`. Anything else gets a proper JSON-RPC error rather than a 404,
because a client that gets HTML back when it expected JSON-RPC fails in
confusing ways.

**Authorisation happens once, at the top, for every method.** There is no
unauthenticated `tools/list`: the tool list is the same for every tenant today,
but making that an authentication exception is how an authentication exception
ends up somewhere it matters.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mabel_mcp.registry import ToolNotFound, Unauthorised, authorise, dispatch, list_tools

logger = logging.getLogger(__name__)

router = APIRouter()

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "mabel", "version": "2.0.0"}

# JSON-RPC 2.0 reserved codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _error(request_id: Any, code: int, message: str, *, status: int = 200) -> JSONResponse:
    """JSON-RPC errors ride on a 200 by default.

    A transport-level 4xx makes a client retry or give up; a JSON-RPC error
    lets the model see what went wrong and carry on talking. The exception is
    authorisation, which returns 401 so the caller knows to re-mint rather than
    re-ask.
    """
    return JSONResponse(
        status_code=status,
        content={"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
    )


def _result(request_id: Any, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": result})


@router.post("/mcp")
async def mcp_endpoint(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a client problem
        return _error(None, PARSE_ERROR, "request body was not JSON")

    if not isinstance(payload, dict):
        # Batch requests are legal JSON-RPC and we do not accept them: a batch
        # spanning two tool calls would need two transactions, and doing that
        # inside one request means an ambiguous partial failure.
        return _error(None, INVALID_REQUEST, "batch requests are not supported")

    request_id = payload.get("id")
    method = payload.get("method")

    try:
        token = authorise(request.headers.get("authorization"))
    except Unauthorised as exc:
        # No detail about *why*. An unauthenticated caller learning whether a
        # token was expired or forged is a caller learning something.
        logger.warning("unauthorised MCP request: %s", exc)
        return _error(request_id, INVALID_REQUEST, "unauthorized", status=401)

    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        )

    if method == "tools/list":
        return _result(request_id, {"tools": list_tools()})

    if method == "tools/call":
        params = payload.get("params") or {}
        name = params.get("name")
        if not isinstance(name, str):
            return _error(request_id, INVALID_PARAMS, "tools/call needs a tool name")

        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(request_id, INVALID_PARAMS, "arguments must be an object")

        try:
            result = await dispatch(name, arguments, token=token)
        except ToolNotFound:
            return _error(request_id, METHOD_NOT_FOUND, f"no tool named {name!r}")
        except Exception:  # noqa: BLE001 - the call must survive a broken tool
            # dispatch() already swallows handler failures; reaching here means
            # the transaction itself failed, which is worth a trace but still
            # must not be a 500 into a live conversation.
            logger.exception("dispatch failed for %s on call %s", name, token.call_id)
            return _error(request_id, INTERNAL_ERROR, "tool call failed")

        logger.info(
            "mcp tool call",
            extra={
                "tool": name,
                "call_id": token.call_id,
                # tenant_id for correlation in Axiom. Not the token.
                "tenant_id": str(token.tenant_id),
                "duration_ms": result.duration_ms,
                "ok": not result.is_error,
            },
        )
        return _result(request_id, result.as_mcp())

    if method in {"notifications/initialized", "ping"}:
        return _result(request_id, {})

    return _error(request_id, METHOD_NOT_FOUND, f"unknown method {method!r}")


@router.get("/mcp/health")
async def health() -> JSONResponse:
    """Unauthenticated, and says nothing about any tenant.

    Better Stack polls this. It reports that the process is up and how many
    tools it is serving, which is enough to catch a deploy that shipped a
    truncated tool list.
    """
    return JSONResponse(content={"status": "ok", "tools": len(list_tools())})
