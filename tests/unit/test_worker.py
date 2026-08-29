"""The worker loop and the Telnyx client, without a database or a network.

The queue's SQL is exercised in `tests/isolation/`. What is here is the
behaviour around it: that one bad job never stops the loop, that shutdown
drains, and that nothing is ever recorded as sent when it was not.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from mabel_telnyx.client import (
    FakeTelnyxClient,
    SendFailed,
    TelnyxRefusedUnderTest,
    TelnyxUnavailable,
    api_key,
    delivery_risk,
    segments_for,
    sip_connection_settings,
)
from mabel_telnyx.webhooks import (
    MAX_AGE_SECONDS,
    PublicKeyUnavailable,
    TelnyxWebhookError,
    signed_payload,
)
from mabel_telnyx.webhooks import verify as verify_telnyx
from mabel_worker.jobs.monthly_report import previous_month
from mabel_worker.queue import BACKOFF_SECONDS, Job, worker_id
from mabel_worker.runner import Runner, build_registry, run_one

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def job(**overrides) -> Job:
    base = {
        "id": 1,
        "tenant_id": uuid4(),
        "kind": "morning_recap",
        "payload": {},
        "attempts": 1,
        "max_attempts": 5,
        "created_at": NOW,
    }
    return Job(**(base | overrides))


class FakeEngine:
    """Stands in for the AsyncEngine the queue functions take. They are
    monkeypatched in these tests, so it is never actually used."""


class TestOneBadJobDoesNotStopTheLoop:
    async def test_a_raising_handler_is_retried_not_propagated(self, monkeypatch):
        retried: list[tuple[int, str]] = []

        async def fake_retry(engine, failing, error):
            retried.append((failing.id, error))

        async def fake_complete(engine, job_id):
            raise AssertionError("a failed job must not be marked complete")

        monkeypatch.setattr("mabel_worker.runner.queue.retry_later", fake_retry)
        monkeypatch.setattr("mabel_worker.runner.queue.complete", fake_complete)

        async def exploding(_job, _engine):
            raise RuntimeError("telnyx fell over")

        await run_one(job(), FakeEngine(), {"morning_recap": exploding})
        assert retried and "telnyx fell over" in retried[0][1]

    async def test_a_successful_job_is_completed(self, monkeypatch):
        completed: list[int] = []

        async def fake_complete(engine, job_id):
            completed.append(job_id)

        monkeypatch.setattr("mabel_worker.runner.queue.complete", fake_complete)

        async def fine(_job, _engine):
            return None

        await run_one(job(), FakeEngine(), {"morning_recap": fine})
        assert completed == [1]

    async def test_an_unknown_kind_fails_immediately_rather_than_retrying(self, monkeypatch):
        """Retrying a kind nothing handles burns the attempts and hides the
        real problem, which is a missing handler."""
        failed: list[str] = []

        async def fake_fail(engine, job_id, error):
            failed.append(error)

        async def fake_retry(engine, failing, error):
            raise AssertionError("an unknown kind should not be retried")

        monkeypatch.setattr("mabel_worker.runner.queue.fail", fake_fail)
        monkeypatch.setattr("mabel_worker.runner.queue.retry_later", fake_retry)

        await run_one(job(kind="does_not_exist"), FakeEngine(), {})
        assert failed and "no handler" in failed[0]

    async def test_the_loop_survives_the_database_going_away(self, monkeypatch):
        """Crash-looping the process on Fly means a restart storm."""
        calls = {"n": 0}

        async def fake_claim(engine, limit=5):
            calls["n"] += 1
            raise OSError("connection refused")

        monkeypatch.setattr("mabel_worker.runner.queue.claim", fake_claim)
        monkeypatch.setattr("mabel_worker.runner.IDLE_SLEEP_SECONDS", 0.01)

        runner = Runner(FakeEngine(), registry={})
        import asyncio

        task = asyncio.create_task(runner.run_forever())
        await asyncio.sleep(0.05)
        runner.request_stop()
        await asyncio.wait_for(task, timeout=1)
        assert calls["n"] >= 1


class TestShutdownDrains:
    async def test_stopping_finishes_the_batch_rather_than_dropping_it(self, monkeypatch):
        """Fly sends SIGTERM on deploy. Exiting immediately leaves claimed jobs
        locked until the lease expires five minutes later, which for a 7am
        recap means it arrives at 7:05 or not at all."""
        ran: list[int] = []
        claimed = {"done": False}

        async def fake_claim(engine, limit=5):
            if claimed["done"]:
                return []
            claimed["done"] = True
            return [job(id=1), job(id=2), job(id=3)]

        async def fake_complete(engine, job_id):
            ran.append(job_id)

        monkeypatch.setattr("mabel_worker.runner.queue.claim", fake_claim)
        monkeypatch.setattr("mabel_worker.runner.queue.complete", fake_complete)
        monkeypatch.setattr("mabel_worker.runner.IDLE_SLEEP_SECONDS", 0.01)

        async def fine(_job, _engine):
            return None

        runner = Runner(FakeEngine(), registry={"morning_recap": fine})
        import asyncio

        task = asyncio.create_task(runner.run_forever())
        await asyncio.sleep(0.05)
        runner.request_stop()
        await asyncio.wait_for(task, timeout=1)
        assert ran == [1, 2, 3]


class TestBackoff:
    def test_it_grows_and_is_capped(self):
        # A Telnyx outage must not retry a thousand recaps into a tight loop.
        assert list(BACKOFF_SECONDS) == sorted(BACKOFF_SECONDS)
        assert BACKOFF_SECONDS[0] == 30
        assert BACKOFF_SECONDS[-1] == 3600

    def test_the_last_attempt_is_recognised(self):
        assert job(attempts=4, max_attempts=5).is_last_attempt is True
        assert job(attempts=1, max_attempts=5).is_last_attempt is False

    def test_the_worker_id_names_a_machine_and_a_process(self):
        # So an abandoned lease can be traced rather than guessed at.
        assert ":" in worker_id()


class TestTheRegistryIsComplete:
    def test_every_cron_kind_has_a_handler(self):
        """A cron entry with no handler fails loudly every time it fires. These
        are the kinds 0002_scheduled_jobs enqueues."""
        registry = build_registry()
        for kind in (
            "morning_recap",
            "followup_nudge",
            "silence_alert",
            "monthly_report",
            "purge_recording",
        ):
            assert kind in registry, f"cron enqueues {kind} with no handler"

    def test_the_sending_job_is_registered(self):
        assert "send_notification" in build_registry()


class TestTelnyxFailsClosed:
    def test_no_key_means_no_client(self, monkeypatch):
        monkeypatch.delenv("TELNYX_API_KEY", raising=False)
        with pytest.raises(TelnyxUnavailable, match="BLOCKED"):
            api_key()

    def test_the_client_refuses_under_pytest(self, monkeypatch):
        monkeypatch.setenv("TELNYX_API_KEY", "would-not-be-used")
        from mabel_telnyx.client import TelnyxClient

        with pytest.raises(TelnyxRefusedUnderTest, match="wake somebody up"):
            TelnyxClient()

    def test_build_client_returns_none_when_the_key_is_missing(self, monkeypatch):
        """Unconfigured is failed-because-unconfigured, not a raised job."""
        monkeypatch.delenv("TELNYX_API_KEY", raising=False)
        from mabel_worker.jobs.send_notification import build_client

        assert build_client() is None

    def test_build_client_returns_none_under_pytest(self, monkeypatch):
        """The construction guard must not kill the send job.

        TelnyxClient raises TelnyxRefusedUnderTest under pytest even when a
        key is present. If build_client lets that escape, the worker records
        a dead job instead of marking each notification failed-unconfigured.
        """
        monkeypatch.setenv("TELNYX_API_KEY", "would-not-be-used")
        from mabel_worker.jobs.send_notification import build_client

        assert build_client() is None

    def test_delivery_risk_names_the_dangerous_state(self, monkeypatch):
        """`unregistered` is the one that matters: the API accepts the message,
        returns an id, and carriers drop it. Everything looks healthy and the
        owner gets nothing."""
        monkeypatch.delenv("TELNYX_API_KEY", raising=False)
        assert delivery_risk() == "no_key"

        monkeypatch.setenv("TELNYX_API_KEY", "x")
        monkeypatch.delenv("TELNYX_10DLC_CAMPAIGN_ID", raising=False)
        assert delivery_risk() == "unregistered"

        monkeypatch.setenv("TELNYX_10DLC_CAMPAIGN_ID", "y")
        assert delivery_risk() == "ok"


class TestSegments:
    def test_a_short_message_is_one_segment(self):
        assert segments_for("x" * 160) == 1

    def test_a_longer_one_costs_more(self):
        # Concatenated messages carry a header, so the budget drops to 153.
        assert segments_for("x" * 161) == 2
        assert segments_for("x" * 306) == 2
        assert segments_for("x" * 307) == 3


class TestFakeTelnyx:
    async def test_it_records_rather_than_sends(self):
        client = FakeTelnyxClient()
        sent = await client.send_sms(
            to_e164="216-555-0148", body="EMERGENCY - burst pipe", from_e164="+12165550199"
        )
        assert sent.to_e164 == "+12165550148"
        assert client.bodies == ["EMERGENCY - burst pipe"]

    async def test_it_can_be_made_to_fail(self):
        client = FakeTelnyxClient(fail_with="carrier rejected")
        with pytest.raises(SendFailed):
            await client.send_sms(to_e164="+12165550148", body="x", from_e164="+12165550199")


class TestTelnyxWebhooks:
    """Telnyx signs with Ed25519, not HMAC. A separate module from the xAI
    verifier on purpose: two schemes behind one function is how one of them
    ends up silently unverified."""

    def test_the_payload_uses_a_pipe_not_a_dot(self):
        # xAI's Standard Webhooks construction uses dots. Using the wrong
        # separator fails every signature with no useful clue why.
        assert signed_payload("123", b"{}") == b"123|{}"

    def test_a_dict_body_is_refused(self):
        with pytest.raises(TelnyxWebhookError, match="must be bytes"):
            verify_telnyx({"a": 1}, {}, key="x")  # type: ignore[arg-type]

    def test_missing_headers_are_refused(self):
        with pytest.raises(TelnyxWebhookError, match="missing"):
            verify_telnyx(b"{}", {}, key="x")

    def test_an_old_webhook_is_refused_before_any_crypto(self):
        headers = {
            "telnyx-signature-ed25519": "irrelevant",
            "telnyx-timestamp": str(int(1_800_000_000 - MAX_AGE_SECONDS - 1)),
        }
        with pytest.raises(TelnyxWebhookError, match="old"):
            verify_telnyx(b"{}", headers, key="x", now=1_800_000_000)

    def test_no_public_key_fails_closed(self, monkeypatch):
        monkeypatch.delenv("TELNYX_PUBLIC_KEY", raising=False)
        headers = {
            "telnyx-signature-ed25519": "x",
            "telnyx-timestamp": str(1_800_000_000),
        }
        with pytest.raises(PublicKeyUnavailable, match="BLOCKED"):
            verify_telnyx(b"{}", headers, now=1_800_000_000)

    def test_a_real_signature_verifies_and_a_tampered_body_does_not(self):
        import base64

        from nacl.signing import SigningKey

        signing = SigningKey.generate()
        public = base64.b64encode(bytes(signing.verify_key)).decode()
        timestamp = str(1_800_000_000)
        body = b'{"event_type":"message.received"}'
        signature = base64.b64encode(
            signing.sign(signed_payload(timestamp, body)).signature
        ).decode()
        headers = {
            "telnyx-signature-ed25519": signature,
            "telnyx-timestamp": timestamp,
        }

        verify_telnyx(body, headers, key=public, now=1_800_000_000)

        with pytest.raises(TelnyxWebhookError):
            verify_telnyx(body + b" ", headers, key=public, now=1_800_000_000)


class TestSipSettings:
    def test_they_match_the_verified_notes(self):
        settings = sip_connection_settings("+12165550148")
        assert settings["fqdn"] == "sip.voice.x.ai"
        assert settings["transport"] == "tls"
        assert settings["origin"] == "byo_trunk"
        assert settings["codecs"] == ["G711U"]
        assert settings["inbound_uri"].startswith("sip:+12165550148@")


class TestMonthBoundaries:
    @pytest.mark.parametrize(
        ("today", "start", "end"),
        [
            ("2026-11-01", "2026-10-01", "2026-10-31"),
            ("2026-01-01", "2025-12-01", "2025-12-31"),
            ("2026-03-01", "2026-02-01", "2026-02-28"),
            ("2028-03-01", "2028-02-01", "2028-02-29"),  # leap year
        ],
    )
    def test_the_previous_month_is_computed_correctly(self, today, start, end):
        from datetime import date

        got_start, got_end = previous_month(date.fromisoformat(today))
        assert got_start == date.fromisoformat(start)
        assert got_end == date.fromisoformat(end)
