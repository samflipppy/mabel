"""Streamable HTTP MCP endpoint. stdio is not used — xAI cannot talk stdio."""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from mabel.mcp.tokens import TokenError, bearer_tenant
from mabel.mcp.tools import TOOL_SCHEMAS, ToolError, bind_tenant, call_tool, reset_tenant
from mabel.platform.config import ConfigError

PROTOCOL_VERSION = "2025-03-26"


def mcp_asgi_app() -> FastAPI:
    app = FastAPI(title="Mabel MCP", docs_url=None, redoc_url=None)
    app.post("/")(handle_mcp)
    app.post("/{path:path}")(handle_mcp)
    return app


async def handle_mcp(request: Request, path: str | None = None) -> Response:
    try:
        tenant_id = bearer_tenant(request.headers.get("authorization"))
    except TokenError as exc:
        return JSONResponse({"error": str(exc)}, status_code=401)
    except ConfigError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Mabel could not read that MCP request."}, status_code=400)

    token = bind_tenant(tenant_id)
    try:
        result = dispatch(payload)
    except ToolError as exc:
        result = _rpc_error(payload, 400, str(exc))
    finally:
        reset_tenant(token)

    return JSONResponse(
        result,
        headers={
            "Content-Type": "application/json",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        },
    )


def dispatch(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return _rpc_error({}, -32600, "Mabel expected a JSON-RPC object.")
    method = payload.get("method")
    rpc_id = payload.get("id")
    params = payload.get("params") or {}

    if method == "initialize":
        return _ok(
            rpc_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mabel", "version": "0.1.0"},
            },
        )
    if method == "notifications/initialized":
        return _ok(rpc_id, {})
    if method == "tools/list":
        return _ok(rpc_id, {"tools": TOOL_SCHEMAS})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str):
            return _rpc_error(payload, -32602, "Mabel needs a tool name.")
        content = call_tool(name, arguments if isinstance(arguments, dict) else {})
        return _ok(
            rpc_id,
            {
                "content": [{"type": "text", "text": json.dumps(content)}],
                "structuredContent": content,
                "isError": False,
            },
        )
    return _rpc_error(payload, -32601, "Mabel does not know that MCP method.")


def _ok(rpc_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _rpc_error(payload: dict[str, Any], code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": payload.get("id") if isinstance(payload, dict) else None,
        "error": {"code": code, "message": message},
    }
