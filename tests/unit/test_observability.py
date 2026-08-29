"""Spans and structured logs.

The thing worth testing is what does *not* come out: a span attribute ends up
in Axiom, in a Sentry breadcrumb, and in whatever gets pasted into a support
thread.
"""

from __future__ import annotations

import json
import logging

import pytest

from mabel_db.observability import alert, current_call, set_call, span


def emitted(caplog) -> list[dict]:
    return [json.loads(record.message) for record in caplog.records if record.name == "mabel.span"]


class TestCorrelation:
    def test_the_call_id_is_on_every_span(self, caplog):
        """When a contractor says "the 2am call went wrong", the answer has to
        be one query returning everything that happened on it."""
        set_call("call_abc", "11111111-1111-1111-1111-111111111111")
        with caplog.at_level(logging.INFO), span("resolve_tenant"):
            pass
        record = emitted(caplog)[0]
        assert record["call_id"] == "call_abc"
        assert record["tenant_id"] == "11111111-1111-1111-1111-111111111111"

    def test_it_is_readable_back(self):
        set_call("call_xyz")
        assert current_call() == "call_xyz"


class TestNothingSensitiveEscapes:
    @pytest.mark.parametrize(
        "key", ["authorization", "token", "access_token", "api_key", "secret", "signature"]
    )
    def test_credential_keys_are_redacted(self, caplog, key):
        set_call("call_abc")
        with caplog.at_level(logging.INFO), span("tool_call", **{key: "sk_live_abc123"}):
            pass
        record = emitted(caplog)[0]
        assert record[key] == "<redacted>"
        assert "sk_live" not in json.dumps(record)

    def test_a_phone_number_is_redacted_whatever_it_is_called(self, caplog):
        """The call site that forgets is the one carrying the number, so this
        is applied to every attribute rather than left to each call site."""
        set_call("call_abc")
        with caplog.at_level(logging.INFO), span("lookup", caller="+1 216 555 0148"):
            pass
        record = emitted(caplog)[0]
        assert record["caller"] == "<phone>"

    def test_an_email_is_redacted(self, caplog):
        set_call("call_abc")
        with caplog.at_level(logging.INFO), span("invite", who="ray@example.com"):
            pass
        assert emitted(caplog)[0]["who"] == "<email>"

    def test_ordinary_identifiers_survive(self, caplog):
        # Redacting everything makes the logs useless, which is how logging
        # gets turned off.
        set_call("call_abc")
        with caplog.at_level(logging.INFO), span("tool", tool="create_lead", rows=3):
            pass
        record = emitted(caplog)[0]
        assert record["tool"] == "create_lead"
        assert record["rows"] == 3


class TestSpans:
    def test_a_duration_is_recorded(self, caplog):
        set_call("call_abc")
        with caplog.at_level(logging.INFO), span("slow_thing"):
            pass
        assert "duration_ms" in emitted(caplog)[0]

    def test_the_body_can_add_attributes_it_learns_late(self, caplog):
        set_call("call_abc")
        with caplog.at_level(logging.INFO), span("query") as attributes:
            attributes["rows"] = 12
        assert emitted(caplog)[0]["rows"] == 12

    def test_a_failure_is_emitted_and_re_raised(self, caplog):
        set_call("call_abc")
        with caplog.at_level(logging.INFO), pytest.raises(RuntimeError):  # noqa: SIM117
            with span("archive"):
                raise RuntimeError("row: Henderson, 216-555-0148")

        record = emitted(caplog)[0]
        assert record["ok"] is False
        assert record["error"] == "RuntimeError"
        # The type, never the message: an exception message carries row
        # contents, and this line is going to Axiom.
        assert "Henderson" not in json.dumps(record)

    def test_alerts_are_distinguishable_from_errors(self, caplog):
        """Better Stack pages on these. They have to be findable."""
        set_call("call_abc")
        with caplog.at_level(logging.ERROR):
            alert("xai concurrency at the ceiling", active_sessions=10)
        record = json.loads(caplog.records[-1].message)
        assert record["alert"] == "xai concurrency at the ceiling"
        assert record["active_sessions"] == 10
