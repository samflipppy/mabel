"""Integrations.

None of these reach a provider. What is under test is the parts that go wrong
without one: the SSRF guard, the GraphQL errors that arrive with a 200, the
interval arithmetic, and the rule that no credential is ever written down.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from mabel_integrations.base import (
    Credentials,
    LeadPayload,
    Provider,
    PushResult,
    VaultUnavailable,
    is_connectable,
    read_credentials,
    redact,
)
from mabel_integrations.google_calendar import BusyInterval, GoogleCalendar, free_slots
from mabel_integrations.housecall import HousecallPro
from mabel_integrations.jobber import Jobber
from mabel_integrations.outbound_webhook import (
    OutboundWebhook,
    UnsafeWebhookUrl,
    WebhookConfig,
    assert_safe_url,
    generate_secret,
    sign,
)

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
TOKEN = Credentials(access_token="not-a-real-token", account_id="acct_1")


def lead(**overrides) -> LeadPayload:
    base = {
        "lead_id": uuid4(),
        "caller_name": "Pat Example",
        "phone_e164": "+12165550148",
        "address": "100 Example Ave",
        "job_type": "burst pipe",
        "description": "Water in the basement",
        "urgency": "emergency",
        "source": "google",
        "created_at": NOW,
    }
    return LeadPayload(**(base | overrides))


class TestCredentialsAreNeverWrittenDown:
    def test_repr_does_not_leak_the_token(self):
        """A token in a traceback ends up in Sentry, in a log aggregator, and
        in a screenshot in a support thread."""
        text = repr(Credentials(access_token="ya29.super-secret", account_id="acct_1"))
        assert "ya29" not in text
        assert "<redacted>" in text
        assert "acct_1" in text

    @pytest.mark.parametrize(
        "key", ["access_token", "refresh_token", "authorization", "client_secret", "api_key"]
    )
    def test_redact_strips_credential_keys(self, key):
        cleaned = redact({key: "secret", "title": "Burst pipe"})
        assert cleaned[key] == "<redacted>"
        assert cleaned["title"] == "Burst pipe"

    async def test_reading_a_token_fails_closed_without_a_vault(self, monkeypatch):
        """The alternative — falling back to integrations.config — is exactly
        what the schema comment and the domain validator exist to prevent."""
        monkeypatch.delenv("SUPABASE_VAULT_URL", raising=False)
        with pytest.raises(VaultUnavailable, match="BLOCKED"):
            await read_credentials("tenant/abc/jobber")

    async def test_no_vault_key_is_refused(self):
        with pytest.raises(VaultUnavailable):
            await read_credentials(None)

    def test_connectability_is_reported_honestly(self, monkeypatch):
        monkeypatch.delenv("JOBBER_CLIENT_ID", raising=False)
        assert is_connectable(Provider.JOBBER) is False
        monkeypatch.setenv("JOBBER_CLIENT_ID", "abc")
        assert is_connectable(Provider.JOBBER) is True


class TestNoLeadPayloadCarriesMoney:
    def test_the_payload_has_no_value_field(self):
        """A job value is owner-entered and lives in Mabel. Pushing it into
        somebody else's system as though it were an estimate is not ours to
        do."""
        fields = set(LeadPayload.__dataclass_fields__)
        assert not any(
            name in fields for name in ("value", "value_cents", "amount", "price", "estimate")
        )

    def test_the_summary_carries_no_figure(self):
        assert "$" not in lead().summary()

    def test_an_emergency_is_marked_in_the_summary(self):
        assert lead().summary().startswith("EMERGENCY")
        assert not lead(urgency="routine").summary().startswith("EMERGENCY")


class TestTheSsrfGuard:
    @pytest.mark.parametrize(
        "url",
        [
            "https://localhost/hook",
            "https://127.0.0.1/hook",
            "https://169.254.169.254/latest/meta-data/",
            "https://0.0.0.0/hook",
        ],
    )
    def test_private_addresses_are_refused(self, url):
        """169.254.169.254 is the cloud metadata endpoint. Sending a customer's
        webhook there turns our worker into a proxy into our own network."""
        with pytest.raises(UnsafeWebhookUrl):
            assert_safe_url(url)

    def test_plaintext_is_refused(self):
        # Lead data on the wire in the clear.
        with pytest.raises(UnsafeWebhookUrl, match="https"):
            assert_safe_url("http://example.com/hook")

    def test_an_unresolvable_host_is_refused(self):
        with pytest.raises(UnsafeWebhookUrl, match="resolve"):
            assert_safe_url("https://this-host-does-not-exist.invalid/hook")

    def test_a_public_url_is_accepted(self):
        assert_safe_url("https://hooks.zapier.com/hooks/catch/1/2/")


class TestOutboundWebhook:
    async def test_it_signs_what_it_sends(self):
        """Signed so a customer's endpoint can tell our requests from anybody
        else's. Same construction we verify from xAI, so a receiver can follow
        the Standard Webhooks docs rather than ours."""
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content
            captured["timestamp"] = request.headers["webhook-timestamp"]
            captured["signature"] = request.headers["webhook-signature"]
            return httpx.Response(200)

        client = OutboundWebhook(transport=httpx.MockTransport(handler))
        result = await client.send(
            WebhookConfig(url="https://hooks.zapier.com/x", secret="whsec_test"), lead()
        )
        await client.aclose()

        assert result.ok
        expected_ts, expected_sig = sign(
            "whsec_test", captured["body"], timestamp=int(captured["timestamp"])
        )
        assert captured["signature"] == expected_sig

    async def test_the_signed_bytes_are_the_sent_bytes(self):
        """Serialised once. Signing one rendering and sending another is the
        same bug we guard against on every inbound webhook."""
        captured: dict[str, bytes] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.content
            return httpx.Response(200)

        client = OutboundWebhook(transport=httpx.MockTransport(handler))
        await client.send(WebhookConfig(url="https://hooks.zapier.com/x", secret="s"), lead())
        await client.aclose()
        # Round-tripping produces different bytes, which is the point.
        assert json.dumps(json.loads(captured["body"])).encode() != captured["body"]

    async def test_a_redirect_is_reported_rather_than_followed(self):
        """Following one would walk straight past the address check — a public
        URL that 302s to the metadata endpoint."""
        client = OutboundWebhook(
            transport=httpx.MockTransport(
                lambda _r: httpx.Response(302, headers={"location": "https://169.254.169.254/"})
            )
        )
        result = await client.send(
            WebhookConfig(url="https://hooks.zapier.com/x", secret="s"), lead()
        )
        await client.aclose()
        assert result.ok is False
        assert "redirect" in (result.error or "")

    async def test_a_private_url_is_refused_at_send_time_too(self):
        """Checked at save time and again here, because DNS can change between
        the two — which is the DNS-rebinding trick."""
        client = OutboundWebhook(transport=httpx.MockTransport(lambda _r: httpx.Response(200)))
        result = await client.send(WebhookConfig(url="https://127.0.0.1/x", secret="s"), lead())
        await client.aclose()
        assert result.ok is False

    async def test_an_error_status_is_reported_not_swallowed(self):
        client = OutboundWebhook(transport=httpx.MockTransport(lambda _r: httpx.Response(500)))
        result = await client.send(
            WebhookConfig(url="https://hooks.zapier.com/x", secret="s"), lead()
        )
        await client.aclose()
        assert result.ok is False
        assert "500" in (result.error or "")

    def test_the_secret_is_generated_not_chosen(self):
        # A secret somebody types is a secret somebody reuses.
        secret = generate_secret()
        assert secret.startswith("whsec_")
        assert len(secret) > 30
        assert secret != generate_secret()


class TestJobber:
    async def test_a_graphql_user_error_is_a_failure(self):
        """Jobber returns user errors with a 200. Checking only the status code
        would report every one of them as a success, which is the silent
        integration failure this package exists to avoid."""

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if "FindClient" in body["query"]:
                return httpx.Response(200, json={"data": {"clients": {"nodes": [{"id": "c1"}]}}})
            return httpx.Response(
                200,
                json={
                    "data": {
                        "requestCreate": {
                            "request": None,
                            "userErrors": [{"message": "Client is archived"}],
                        }
                    }
                },
            )

        client = Jobber(transport=httpx.MockTransport(handler))
        result = await client.push_lead(lead(), TOKEN)
        await client.aclose()

        assert result.ok is False
        assert "archived" in (result.error or "")

    async def test_a_transport_level_graphql_error_is_a_failure(self):
        client = Jobber(
            transport=httpx.MockTransport(
                lambda _r: httpx.Response(200, json={"errors": [{"message": "Bad token"}]})
            )
        )
        result = await client.push_lead(lead(), TOKEN)
        await client.aclose()
        assert result.ok is False
        assert "Bad token" in (result.error or "")

    async def test_a_successful_push_returns_the_request_id(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if "FindClient" in body["query"]:
                return httpx.Response(200, json={"data": {"clients": {"nodes": [{"id": "c1"}]}}})
            return httpx.Response(
                200,
                json={
                    "data": {
                        "requestCreate": {
                            "request": {"id": "req_1", "title": "x"},
                            "userErrors": [],
                        }
                    }
                },
            )

        client = Jobber(transport=httpx.MockTransport(handler))
        result = await client.push_lead(lead(), TOKEN)
        await client.aclose()
        assert result.ok is True
        assert result.external_ref == "req_1"

    async def test_an_existing_client_is_matched_not_duplicated(self):
        """A contractor's existing customer should not become a duplicate
        because they rang after hours. Matched on phone, deterministically —
        the same rule Mabel's own contact resolution uses."""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            seen.append(body["query"])
            if "FindClient" in body["query"]:
                return httpx.Response(200, json={"data": {"clients": {"nodes": [{"id": "c1"}]}}})
            return httpx.Response(
                200,
                json={"data": {"requestCreate": {"request": {"id": "r"}, "userErrors": []}}},
            )

        client = Jobber(transport=httpx.MockTransport(handler))
        await client.push_lead(lead(), TOKEN)
        await client.aclose()
        assert not any("CreateClient" in query for query in seen)

    async def test_the_pushed_payload_carries_no_money(self):
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            captured.append(body)
            if "FindClient" in body["query"]:
                return httpx.Response(200, json={"data": {"clients": {"nodes": [{"id": "c1"}]}}})
            return httpx.Response(
                200,
                json={"data": {"requestCreate": {"request": {"id": "r"}, "userErrors": []}}},
            )

        client = Jobber(transport=httpx.MockTransport(handler))
        await client.push_lead(lead(), TOKEN)
        await client.aclose()
        # The variables, not the query text: `$` is GraphQL variable syntax
        # and appears in every query we send.
        blob = json.dumps([body["variables"] for body in captured])
        assert "$" not in blob
        assert "value_cents" not in blob


class TestGoogleCalendar:
    def test_a_busy_interval_blocks_an_overlapping_slot(self):
        busy = [BusyInterval(NOW, NOW + timedelta(hours=2))]
        candidates = [
            (NOW + timedelta(minutes=30), NOW + timedelta(hours=1)),
            (NOW + timedelta(hours=3), NOW + timedelta(hours=4)),
        ]
        assert free_slots(busy, candidates) == [candidates[1]]

    def test_touching_intervals_do_not_overlap(self):
        # A meeting ending at 10 does not block a slot starting at 10.
        busy = [BusyInterval(NOW, NOW + timedelta(hours=1))]
        candidate = (NOW + timedelta(hours=1), NOW + timedelta(hours=2))
        assert free_slots(busy, [candidate]) == [candidate]

    def test_no_busy_time_leaves_everything_free(self):
        candidates = [(NOW, NOW + timedelta(hours=1))]
        assert free_slots([], candidates) == candidates

    async def test_an_api_failure_raises_rather_than_reporting_free(self):
        """ "No busy time" and "we could not ask" must not look the same. The
        first offers every slot; the second should offer none."""
        from mabel_integrations.base import IntegrationError

        client = GoogleCalendar(transport=httpx.MockTransport(lambda _r: httpx.Response(500)))
        with pytest.raises(IntegrationError):
            await client.busy_intervals(TOKEN)
        await client.aclose()

    async def test_pushing_a_lead_is_a_no_op(self):
        client = GoogleCalendar(transport=httpx.MockTransport(lambda _r: httpx.Response(200)))
        result = await client.push_lead(lead(), TOKEN)
        await client.aclose()
        assert result.ok is True
        assert "skipped" in result.payload

    async def test_an_appointment_carries_no_figure(self):
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "evt_1"})

        client = GoogleCalendar(transport=httpx.MockTransport(handler))
        result = await client.create_appointment(
            TOKEN, starts_at=NOW, ends_at=NOW + timedelta(hours=1), lead=lead()
        )
        await client.aclose()
        assert result.ok and result.external_ref == "evt_1"
        # A job value in a calendar entry appears on his lock screen.
        assert "$" not in json.dumps(captured)


class TestHousecallPro:
    async def test_a_403_names_the_plan_rather_than_permissions(self):
        """A generic 403 would send somebody looking for a permissions problem
        that does not exist."""
        client = HousecallPro(transport=httpx.MockTransport(lambda _r: httpx.Response(403)))
        result = await client.push_lead(lead(), TOKEN)
        await client.aclose()
        assert result.ok is False
        assert "MAX" in (result.error or "")

    async def test_an_emergency_is_marked_high_priority(self):
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "lead_1"})

        client = HousecallPro(transport=httpx.MockTransport(handler))
        await client.push_lead(lead(urgency="emergency"), TOKEN)
        await client.aclose()
        assert captured[0]["priority"] == "high"


class TestPushResult:
    def test_a_failure_carries_a_reason(self):
        result = PushResult(ok=False, error="endpoint returned 500")
        assert result.status == "failed"
        assert result.error

    def test_a_success_carries_a_reference(self):
        result = PushResult(ok=True, external_ref="req_1")
        assert result.status == "ok"
