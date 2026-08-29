"""The "Call Mabel now" button.

The button stays. This endpoint refuses. Mabel never places an AI-voice
outbound call (FCC TCPA) — not to the contractor's mobile, not to a
callback number, not by originating Telnyx and joining grok-voice. She
answers inbound.

Hearing her means calling the business line. That is inbound, the way
she answers.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from mabel_api.deps import CurrentUserDep

router = APIRouter(prefix="/api/config", tags=["config"])


class TestCallResult(BaseModel):
    placed: bool
    calling: str | None
    message: str


@router.post("/test-call", response_model=TestCallResult)
async def refuse_test_call(user: CurrentUserDep) -> TestCallResult:
    """No outbound place-call function. The button is not a live path."""
    del user
    return TestCallResult(
        placed=False,
        calling=None,
        message=(
            "Mabel doesn't place outbound calls. Call your business line "
            "and we'll tell you when it reaches her."
        ),
    )
