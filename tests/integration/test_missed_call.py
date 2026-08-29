"""The missed-call text back.

Built on four unverified assumptions about Telnyx's Call Control payloads
(docs/telnyx_notes.md, T-1 to T-4), so these tests do two jobs. They check the
decision logic, and they pin the payload shape the code expects -- when the
account exists and a real payload disagrees, one of these fails and names the
field, which is cheaper than reading the handler again.

The refusals matter more than the send. Every one of them is a way to text a
person who did not call this business.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabel_api.webhooks import telnyx_calls

pytestmark = pytest.mark.asyncio

DID = "+12165550148"
CALLER = "+12165550100"


@pytest_asyncio.fixture
async def texting_shop(app_engine: AsyncEngine, engine: AsyncEngine):
    tenant_id = uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (id, business_name, trade, status, did_e164, "
                "customer_sms_enabled) "
                "VALUES (:id, 'Ruiz Plumbing', 'plumbing', 'active', :did, true)"
            ),
            {"id": tenant_id, "did": DID},
        )
    yield tenant_id
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant_id})
        await conn.execute(text("DELETE FROM call_legs"))


def hangup(*, leg: str, direction: str = "incoming", frm: str | None = CALLER) -> dict:
    """A `call.hangup` payload, shaped as docs/telnyx_notes.md T-1..T-4 expect."""
    return {
        "call_leg_id": leg,
        "direction": direction,
        "from": frm,
        "to": DID,
        "hangup_cause": "originator_cancel",
    }


def answered(*, leg: str) -> dict:
    return {"call_leg_id": leg, "direction": "incoming", "from": CALLER, "to": DID}


async def _texts(app_engine, tenant_id) -> list[str]:
    async with app_engine.begin() as conn:
        await conn.execute(text(f"SET LOCAL app.tenant_id = '{tenant_id}'"))
        result = await conn.execute(
            text("SELECT body FROM notifications WHERE kind = 'customer_missed_call'")
        )
        return [row[0] for row in result]


class TestItTextsBack:
    async def test_a_caller_who_hung_up_while_ringing_hears_from_us(self, app_engine, texting_shop):
        """The one case the feature exists for. There is no lead, no
        transcript and no name -- only a number."""
        sent = await telnyx_calls._text_back_if_missed(
            hangup(leg=f"leg-{uuid4()}"), engine=app_engine
        )
        assert sent

        texts = await _texts(app_engine, texting_shop)
        assert len(texts) == 1
        assert "Ruiz Plumbing" in texts[0]
        assert "sorry we missed" in texts[0].lower()
        assert "STOP" in texts[0]

    async def test_it_creates_the_contact_so_the_owner_can_see_them(self, app_engine, texting_shop):
        """Without this the business has no record the person existed, and the
        portal shows nothing at all."""
        await telnyx_calls._text_back_if_missed(hangup(leg=f"leg-{uuid4()}"), engine=app_engine)

        async with app_engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL app.tenant_id = '{texting_shop}'"))
            result = await conn.execute(
                text("SELECT primary_phone, sms_consent_at FROM contacts WHERE primary_phone = :p"),
                {"p": CALLER},
            )
            row = result.mappings().one()
        assert row["primary_phone"] == CALLER
        assert row["sms_consent_at"] is not None


class TestItRefuses:
    async def test_an_answered_call_is_never_a_missed_call(self, app_engine, texting_shop):
        """The assumption this whole feature turns on. A caller who spoke to
        Mabel for four minutes must not be told we missed them."""
        leg = f"leg-{uuid4()}"
        await telnyx_calls._remember_answered(answered(leg=leg), engine=app_engine)

        sent = await telnyx_calls._text_back_if_missed(hangup(leg=leg), engine=app_engine)
        assert not sent
        assert await _texts(app_engine, texting_shop) == []

    async def test_an_outbound_call_never_texts_anyone(self, app_engine, texting_shop):
        """ASSUMPTION T-2. Without the direction check, a callback the owner
        placed through us would text the customer "sorry we missed your call"
        immediately after the owner hung up on a conversation they had."""
        sent = await telnyx_calls._text_back_if_missed(
            hangup(leg=f"leg-{uuid4()}", direction="outgoing"), engine=app_engine
        )
        assert not sent
        assert await _texts(app_engine, texting_shop) == []

    async def test_a_hangup_with_no_leg_id_sends_nothing(self, app_engine, texting_shop):
        """ASSUMPTION T-4. No correlation id means we cannot know whether the
        call was answered, and "cannot know" has to resolve to silence."""
        payload = hangup(leg="x")
        for key in ("call_leg_id", "call_session_id", "call_control_id"):
            payload.pop(key, None)

        assert not await telnyx_calls._text_back_if_missed(payload, engine=app_engine)
        assert await _texts(app_engine, texting_shop) == []

    async def test_a_withheld_number_sends_nothing(self, app_engine, texting_shop):
        assert not await telnyx_calls._text_back_if_missed(
            hangup(leg=f"leg-{uuid4()}", frm=None), engine=app_engine
        )
        assert await _texts(app_engine, texting_shop) == []

    async def test_a_did_we_do_not_own_sends_nothing(self, app_engine, texting_shop):
        """Belt and braces on the resolver. A call to a number that is not ours
        cannot produce a message in anyone's name."""
        payload = hangup(leg=f"leg-{uuid4()}")
        payload["to"] = "+19998887777"
        assert not await telnyx_calls._text_back_if_missed(payload, engine=app_engine)
        assert await _texts(app_engine, texting_shop) == []

    async def test_a_shop_with_customer_sms_off_sends_nothing(
        self, app_engine, engine, texting_shop
    ):
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE tenants SET customer_sms_enabled = false WHERE id = :id"),
                {"id": texting_shop},
            )
        assert not await telnyx_calls._text_back_if_missed(
            hangup(leg=f"leg-{uuid4()}"), engine=app_engine
        )
        assert await _texts(app_engine, texting_shop) == []


class TestTheAnswerMarkerOutlivesTheCall:
    async def test_the_marker_is_not_in_the_ten_minute_prune(self, app_engine, texting_shop):
        """`call_legs` and not `webhook_receipts`, and the difference is not
        stylistic.

        Receipts are pruned every ten minutes. An eleven-minute call whose
        answer marker lived there would lose it before the hangup arrived, look
        like a missed call, and text a customer who had just spent eleven
        minutes on the phone. The bug would only ever show on long calls, which
        are the good ones.
        """
        leg = f"leg-{uuid4()}"
        await telnyx_calls._remember_answered(answered(leg=leg), engine=app_engine)

        async with app_engine.begin() as conn:
            in_legs = await conn.execute(
                text("SELECT answered_at FROM call_legs WHERE leg_id = :l"), {"l": leg}
            )
            assert in_legs.scalar_one() is not None

            in_receipts = await conn.execute(
                text("SELECT count(*) FROM webhook_receipts WHERE webhook_id LIKE :p"),
                {"p": f"%{leg}%"},
            )
            assert in_receipts.scalar_one() == 0

    async def test_noting_an_answer_twice_keeps_the_first_time(self, app_engine, texting_shop):
        """Telnyx retries. The second `call.answered` for a leg must not move
        the timestamp, or a retry during a long call would refresh it."""
        leg = f"leg-{uuid4()}"
        await telnyx_calls._remember_answered(answered(leg=leg), engine=app_engine)
        async with app_engine.begin() as conn:
            first = (
                await conn.execute(
                    text("SELECT answered_at FROM call_legs WHERE leg_id = :l"), {"l": leg}
                )
            ).scalar_one()

        await telnyx_calls._remember_answered(answered(leg=leg), engine=app_engine)
        async with app_engine.begin() as conn:
            second = (
                await conn.execute(
                    text("SELECT answered_at FROM call_legs WHERE leg_id = :l"), {"l": leg}
                )
            ).scalar_one()

        assert first == second
