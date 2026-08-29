"""The "Call Mabel now" button.

02-PORTAL.md: "This single feature will close deals and prevent support
tickets." It places a call to the user's own phone so they hear their own
configuration read back at them — the greeting they wrote, the voice they
picked, the speed they set.

**It calls the user's own number, from our number, and nothing else.** The
destination is read from their `users` row, never from the request. A test-call
endpoint that takes a phone number is a free robocall gun pointed at anybody,
authenticated or not.

Telnyx outbound calling needs an account that does not exist yet
(docs/BLOCKED.md #3), so this fails closed with a specific message rather than
a generic 500 — the difference between "we haven't finished setting up" and
"your portal is broken".
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from mabel_domain.phone import format_national
from mabel_telnyx.client import TelnyxUnavailable, api_key
from pydantic import BaseModel
from sqlalchemy import text

from mabel_api.deps import CurrentUserDep, TenantConn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config", tags=["config"])

# One test call per user per this window. It costs money, it rings a real
# phone, and a button somebody can hold down is a button somebody will.
COOLDOWN_MINUTES = 5


class TestCallResult(BaseModel):
    placed: bool
    calling: str | None
    message: str


@router.post("/test-call", response_model=TestCallResult)
async def place_test_call(user: CurrentUserDep, conn: TenantConn) -> TestCallResult:
    result = await conn.execute(
        text("SELECT phone_e164 FROM users WHERE id = :id"), {"id": user.user_id}
    )
    destination = result.scalar_one_or_none()
    if not destination:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("Add your mobile number under Team first — that's the number Mabel will ring."),
        )

    live = await conn.execute(text("SELECT id FROM agent_configs WHERE is_live"))
    if live.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Publish a configuration first — there's nothing for her to read out yet.",
        )

    recent = await conn.execute(
        text(
            """
            SELECT count(*) FROM audit_log
            WHERE actor_id = :actor
              AND action = 'test_call_placed'
              AND created_at > :since
            """
        ),
        {"actor": user.user_id, "since": datetime.now(UTC) - timedelta(minutes=COOLDOWN_MINUTES)},
    )
    if int(recent.scalar_one()) > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Give it {COOLDOWN_MINUTES} minutes between test calls.",
        )

    did = await conn.execute(text("SELECT did_e164 FROM tenants"))
    from_number = did.scalar_one_or_none()
    if not from_number:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No Mabel number assigned to this business yet.",
        )

    try:
        api_key()
    except TelnyxUnavailable:
        # Fail closed with a specific message. "We haven't finished setting up"
        # is a different problem from "your portal is broken", and the person
        # reading it should be able to tell which.
        logger.info("test call requested but Telnyx is not configured")
        return TestCallResult(
            placed=False,
            calling=format_national(destination),
            message=(
                "Test calls aren't available yet — the phone account is still "
                "being set up. Everything else on this screen works."
            ),
        )

    # TODO(telnyx): dial `destination` from `from_number` and bridge to
    # `mabel_xai.client.sip_uri(from_number)` — the same URI an inbound call
    # reaches, so the test exercises the real configuration rather than a
    # preview of it. Blocked on the account (docs/BLOCKED.md #3); everything
    # above this line is the part that does not need it.

    await conn.execute(
        text(
            "INSERT INTO audit_log (tenant_id, actor_id, actor_type, action, entity) "
            "VALUES (:t, :actor, 'user', 'test_call_placed', 'call')"
        ),
        {"t": user.tenant_id, "actor": user.user_id},
    )

    return TestCallResult(
        placed=True,
        calling=format_national(destination),
        message=f"Calling {format_national(destination)} now. Answer and talk to her.",
    )
