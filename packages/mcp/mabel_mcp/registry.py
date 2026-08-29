"""Dispatch. The one place a token becomes a tenant scope.

Every tool call goes through `dispatch()`, and `dispatch()` does the same four
things every time, in the same order:

1. Verify the bearer token. An unverifiable token is not a degraded call, it is
   no call at all.
2. Open `tenant_scope(token.tenant_id)`. The tenant comes from the token, which
   came from the dialed number. Not from `args`.
3. Run the handler inside that transaction.
4. Commit, or roll the whole thing back.

Point 3 is why the emergency path is safe. `escalate_emergency` writes a lead
and queues an SMS, and both happen inside one transaction — so either the owner
gets a text about a lead that exists, or neither happened.

A handler that raises does not take the call down. The model gets a structured
error and can say something sensible; a stack trace mid-conversation is dead
air while a homeowner stands in six inches of water.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from mabel_db.tenant import tenant_scope
from mabel_mcp.repo import DbRepo, Repo, ToolContext
from mabel_mcp.schemas import BY_NAME, TOOL_NAMES
from mabel_mcp.tokens import CallToken, TokenError, from_authorization_header, verify_call_token
from mabel_mcp.tools.area import get_service_area
from mabel_mcp.tools.capture import create_lead, escalate_emergency, log_note
from mabel_mcp.tools.knowledge import answer_question
from mabel_mcp.tools.lookup import get_job_history, lookup_customer
from mabel_mcp.tools.scheduling import book_estimate, check_availability

logger = logging.getLogger(__name__)

Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[dict[str, Any]]]

HANDLERS: dict[str, Handler] = {
    "lookup_customer": lookup_customer,
    "get_service_area": get_service_area,
    "check_availability": check_availability,
    "create_lead": create_lead,
    "escalate_emergency": escalate_emergency,
    "book_estimate": book_estimate,
    "get_job_history": get_job_history,
    "answer_question": answer_question,
    "log_note": log_note,
}


class ToolNotFound(KeyError):
    pass


class Unauthorised(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ToolResult:
    name: str
    content: dict[str, Any]
    is_error: bool = False
    duration_ms: int = 0

    def as_mcp(self) -> dict[str, Any]:
        """MCP wants content as a list of typed parts. We send one JSON part
        rather than prose, so the model gets a structure it can read fields off
        instead of a sentence it has to interpret."""
        import json

        return {
            "content": [{"type": "text", "text": json.dumps(self.content, default=str)}],
            "isError": self.is_error,
        }


def list_tools() -> list[dict[str, Any]]:
    """The MCP `tools/list` response. Exactly the nine, in spec order."""
    return [BY_NAME[name].as_mcp() for name in TOOL_NAMES]


def authorise(authorization_header: str | None, *, key: str | None = None) -> CallToken:
    """Turn a header into a tenant, or refuse.

    Deliberately not forgiving. There is no anonymous mode, no fallback tenant,
    and no path where a tool runs without a verified token.
    """
    try:
        return verify_call_token(from_authorization_header(authorization_header), key=key)
    except TokenError as exc:
        raise Unauthorised(str(exc)) from exc


async def dispatch(
    name: str,
    args: dict[str, Any],
    *,
    token: CallToken,
    engine: AsyncEngine | None = None,
    now: datetime | None = None,
) -> ToolResult:
    """Run one tool call inside its tenant's transaction."""
    started = time.monotonic()
    async with tenant_scope(token.tenant_id, engine=engine) as conn:
        repo = DbRepo(conn=conn, tenant_id=token.tenant_id)
        ctx = ToolContext(repo=repo, call_id=token.call_id, now=now or datetime.now(UTC))
        return await _run(name, args, ctx, started)


async def dispatch_with_repo(
    name: str,
    args: dict[str, Any],
    *,
    repo: Repo,
    call_id: str = "call_test",
    now: datetime | None = None,
) -> ToolResult:
    """Same dispatch, against an injected repo and no database.

    Used by the unit tests and by the simulation harness. It does not skip the
    tenant check — there is nothing to skip, because a `Repo` is already bound
    to one tenant. That is the property that makes the seam safe.
    """
    started = time.monotonic()
    ctx = ToolContext(repo=repo, call_id=call_id, now=now or datetime.now(UTC))
    return await _run(name, args, ctx, started)


async def _run(name: str, args: dict[str, Any], ctx: ToolContext, started: float) -> ToolResult:
    if name not in HANDLERS:
        # An unknown tool name means the session config and this file have
        # drifted. Checked here rather than in dispatch() so both entry points
        # behave identically -- the test path found this by raising a bare
        # KeyError where the live path raised ToolNotFound.
        logger.warning("unknown tool %r requested on call %s", name, ctx.call_id)
        raise ToolNotFound(name)

    handler = HANDLERS[name]
    try:
        content = await handler(ctx, args or {})
    except Exception:  # noqa: BLE001 - a live call must not die on a handler
        # She is mid-conversation with somebody. A structured error lets her
        # say "let me take your details instead"; an exception is dead air.
        logger.exception("tool %s failed on call %s", name, ctx.call_id)
        return ToolResult(
            name=name,
            content={
                "error": "tool_failed",
                # No exception text: it can carry row contents, and it reaches
                # a language model that may read it out loud.
                "message": "That didn't work. Take the caller's details instead.",
            },
            is_error=True,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    return ToolResult(
        name=name,
        content=content,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


@dataclass
class ToolTrace:
    """What she actually did during the call.

    02-PORTAL.md puts this on the call detail screen: 'created lead, texted
    owner'. It builds trust because the owner can see the machine working, and
    it is the input to the QA pass.
    """

    entries: list[dict[str, Any]] = field(default_factory=list)

    def record(self, result: ToolResult, args: dict[str, Any]) -> None:
        self.entries.append(
            {
                "tool": result.name,
                "ok": not result.is_error,
                "duration_ms": result.duration_ms,
                # Arguments are recorded because the trace is evidence. They
                # are the caller's own details, which we already store.
                "args": args,
                "mutating": BY_NAME[result.name].mutating if result.name in BY_NAME else False,
            }
        )

    def called(self, name: str) -> bool:
        return any(entry["tool"] == name for entry in self.entries)

    @property
    def mutations(self) -> list[dict[str, Any]]:
        return [entry for entry in self.entries if entry["mutating"] and entry["ok"]]
