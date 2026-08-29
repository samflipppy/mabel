"""Structured logs and spans, correlated by `call_id`.

00-STACK.md: Axiom, structured JSON, `call_id` correlation. That last part is
the requirement that shapes this file — when a contractor says "the 2am call
went wrong", the answer has to be one query returning the webhook, the session,
every tool call, and the post-call archive.

**No secret and no customer detail ever becomes a span attribute.** Attributes
end up in Axiom, in Sentry breadcrumbs, and in whatever gets pasted into a
support thread. Identifiers are fine; a phone number is not, and a bearer token
certainly is not. `_safe` enforces that rather than trusting each call site.

No OpenTelemetry dependency. Structured JSON on stdout is what Axiom ingests
from Fly anyway, and a tracing SDK would be a large dependency in the media
process — the one place a garbage-collection pause is audible to a homeowner.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from uuid import UUID

logger = logging.getLogger("mabel.span")

# Correlation, carried implicitly so a handler five frames down does not have
# to thread it through every signature.
_call_id: ContextVar[str | None] = ContextVar("call_id", default=None)
_tenant_id: ContextVar[str | None] = ContextVar("tenant_id", default=None)

# Attribute names that must never carry a value.
FORBIDDEN_KEYS = frozenset(
    {
        "authorization",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "secret",
        "password",
        "signature",
    }
)

# Values that look like a phone number or an email, whatever they are called.
_PHONE = re.compile(r"\+?\d[\d\s().-]{8,}\d")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def set_call(call_id: str | None, tenant_id: UUID | str | None = None) -> None:
    """Bind correlation for everything that follows on this task."""
    _call_id.set(call_id)
    _tenant_id.set(str(tenant_id) if tenant_id else None)


def current_call() -> str | None:
    return _call_id.get()


def _safe(key: str, value: Any) -> Any:
    """Redact anything that should not leave the process.

    Applied to every attribute rather than left to each call site, because the
    call site that forgets is the one carrying the token.
    """
    if key.lower() in FORBIDDEN_KEYS:
        return "<redacted>"
    if isinstance(value, str):
        if _EMAIL.search(value):
            return "<email>"
        if _PHONE.search(value):
            # A phone number in a log is a customer's phone number in Axiom.
            return "<phone>"
    return value


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[dict[str, Any]]:
    """Time an operation and emit one structured line when it finishes.

    Yields a mutable dict so the body can add attributes it only learns later —
    a row count, an outcome — without a second logging call.

    Emits on failure too, with the exception type but never its message: an
    exception message can carry a row's contents.
    """
    started = time.monotonic()
    extra: dict[str, Any] = {}
    try:
        yield extra
    except Exception as exc:
        _emit(
            name,
            duration_ms=int((time.monotonic() - started) * 1000),
            ok=False,
            error=type(exc).__name__,
            **{**attributes, **extra},
        )
        raise
    else:
        _emit(
            name,
            duration_ms=int((time.monotonic() - started) * 1000),
            ok=True,
            **{**attributes, **extra},
        )


def _emit(name: str, **attributes: Any) -> None:
    record = {
        "span": name,
        "call_id": _call_id.get(),
        "tenant_id": _tenant_id.get(),
        **{key: _safe(key, value) for key, value in attributes.items()},
    }
    logger.info(json.dumps({k: v for k, v in record.items() if v is not None}, default=str))


def configure_logging(level: str = "INFO") -> None:
    """JSON on stdout, which is what Fly ships to Axiom.

    Called by every process entrypoint. Deliberately not called at import: a
    library that configures logging on import fights with whatever the
    application wanted.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # These two are loud and rarely useful at INFO.
    logging.getLogger("sqlalchemy.engine").setLevel("WARNING")
    logging.getLogger("httpx").setLevel("WARNING")


def alert(message: str, **attributes: Any) -> None:
    """Something a human should look at now.

    Distinct from an error log: this is what Better Stack pages on. Used for
    the small set of things that mean Mabel is not answering — the concurrency
    ceiling, the queue backing up, a run of failed sends.
    """
    logger.error(
        json.dumps(
            {
                "alert": message,
                "call_id": _call_id.get(),
                "tenant_id": _tenant_id.get(),
                **{key: _safe(key, value) for key, value in attributes.items()},
            },
            default=str,
        )
    )
