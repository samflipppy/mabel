"""Webhook verification. Invariant 8, three ways.

The signature construction is an assumption (docs/xai_notes.md A2/A3), so
these tests check that *our* implementation is internally consistent and
refuses everything it should. They cannot tell us the construction is right —
only the first live webhook can do that, and `verify()` logs enough on
mismatch to say so plainly.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from mabel_xai.webhooks import (
    MAX_AGE_SECONDS,
    SecretUnavailable,
    SignatureMismatch,
    TimestampOutOfRange,
    WebhookError,
    signed_payload,
    signing_secret,
    verify,
)

SECRET = "whsec_" + base64.b64encode(b"a-test-signing-secret-not-a-real-one").decode()
BODY = b'{"type":"realtime.call.incoming","call_id":"call_abc","to":"+12165550148"}'
NOW = 1_800_000_000.0


def sign(body: bytes, webhook_id: str, timestamp: str, secret: str = SECRET) -> str:
    key = base64.b64decode(secret[len("whsec_") :])
    digest = hmac.new(key, signed_payload(webhook_id, timestamp, body), hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode()


def headers(
    body: bytes = BODY, *, webhook_id: str = "msg_1", at: float = NOW, secret: str = SECRET
):
    timestamp = str(int(at))
    return {
        "webhook-id": webhook_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": sign(body, webhook_id, timestamp, secret),
    }


class TestTheHappyPath:
    def test_a_correctly_signed_webhook_verifies(self):
        result = verify(BODY, headers(), secret=SECRET, now=NOW)
        assert result.webhook_id == "msg_1"

    def test_header_case_does_not_matter(self):
        raw = headers()
        upper = {k.upper(): v for k, v in raw.items()}
        assert verify(BODY, upper, secret=SECRET, now=NOW).webhook_id == "msg_1"

    def test_a_raw_secret_without_the_prefix_also_works(self):
        plain = "a-plain-secret"
        timestamp = str(int(NOW))
        digest = hmac.new(
            plain.encode(), signed_payload("msg_1", timestamp, BODY), hashlib.sha256
        ).digest()
        given = {
            "webhook-id": "msg_1",
            "webhook-timestamp": timestamp,
            "webhook-signature": "v1," + base64.b64encode(digest).decode(),
        }
        assert verify(BODY, given, secret=plain, now=NOW)

    def test_a_signature_without_a_version_prefix_is_accepted(self):
        raw = headers()
        raw["webhook-signature"] = raw["webhook-signature"].split(",", 1)[1]
        assert verify(BODY, raw, secret=SECRET, now=NOW)

    def test_several_signatures_during_a_rotation_are_all_tried(self):
        raw = headers()
        raw["webhook-signature"] = "v1,ZmFrZQ== " + raw["webhook-signature"]
        assert verify(BODY, raw, secret=SECRET, now=NOW)


class TestTheRawBody:
    def test_a_re_serialised_body_does_not_verify(self):
        """The failure this test exists for. `json.dumps(json.loads(body))`
        reorders keys and changes whitespace, and the signature is over bytes.
        A framework that hands you the parsed dict has already destroyed it."""
        given = headers(BODY)
        round_tripped = json.dumps(json.loads(BODY)).encode()
        assert round_tripped != BODY
        with pytest.raises(SignatureMismatch):
            verify(round_tripped, given, secret=SECRET, now=NOW)

    def test_a_dict_is_refused_outright(self):
        with pytest.raises(WebhookError, match="must be bytes"):
            verify(json.loads(BODY), headers(), secret=SECRET, now=NOW)  # type: ignore[arg-type]

    def test_a_str_is_refused_outright(self):
        with pytest.raises(WebhookError, match="must be bytes"):
            verify(BODY.decode(), headers(), secret=SECRET, now=NOW)  # type: ignore[arg-type]

    def test_one_changed_byte_fails(self):
        tampered = BODY.replace(b"+12165550148", b"+12165550199")
        with pytest.raises(SignatureMismatch):
            verify(tampered, headers(BODY), secret=SECRET, now=NOW)


class TestReplay:
    def test_an_old_webhook_is_refused(self):
        old = headers(at=NOW - MAX_AGE_SECONDS - 1)
        with pytest.raises(TimestampOutOfRange, match="old"):
            verify(BODY, old, secret=SECRET, now=NOW)

    def test_the_boundary_is_accepted(self):
        edge = headers(at=NOW - MAX_AGE_SECONDS)
        assert verify(BODY, edge, secret=SECRET, now=NOW)

    def test_a_little_clock_skew_is_tolerated(self):
        # Our clock a few seconds behind theirs is normal, not an attack.
        ahead = headers(at=NOW + 30)
        assert verify(BODY, ahead, secret=SECRET, now=NOW)

    def test_a_wildly_future_timestamp_is_refused(self):
        with pytest.raises(TimestampOutOfRange, match="future"):
            verify(BODY, headers(at=NOW + 3600), secret=SECRET, now=NOW)

    def test_a_non_numeric_timestamp_is_refused(self):
        bad = headers()
        bad["webhook-timestamp"] = "yesterday"
        with pytest.raises(TimestampOutOfRange):
            verify(BODY, bad, secret=SECRET, now=NOW)

    def test_the_timestamp_is_part_of_what_was_signed(self):
        """Changing the timestamp to slip past the age check invalidates the
        signature, which is the point of including it in the payload."""
        given = headers(at=NOW - MAX_AGE_SECONDS - 1)
        given["webhook-timestamp"] = str(int(NOW))
        with pytest.raises(SignatureMismatch):
            verify(BODY, given, secret=SECRET, now=NOW)


class TestMissingPieces:
    @pytest.mark.parametrize("drop", ["webhook-id", "webhook-timestamp", "webhook-signature"])
    def test_a_missing_header_is_refused(self, drop):
        given = headers()
        del given[drop]
        with pytest.raises(WebhookError, match="missing"):
            verify(BODY, given, secret=SECRET, now=NOW)

    def test_no_headers_at_all(self):
        with pytest.raises(WebhookError, match="missing"):
            verify(BODY, {}, secret=SECRET, now=NOW)

    def test_no_secret_configured_fails_closed(self, monkeypatch):
        monkeypatch.delenv("XAI_WEBHOOK_SECRET", raising=False)
        with pytest.raises(SecretUnavailable, match="BLOCKED"):
            signing_secret()

    def test_verify_without_a_secret_fails_closed(self, monkeypatch):
        monkeypatch.delenv("XAI_WEBHOOK_SECRET", raising=False)
        with pytest.raises(SecretUnavailable):
            verify(BODY, headers(), now=NOW)

    def test_the_wrong_secret_does_not_verify(self):
        other = "whsec_" + base64.b64encode(b"a-completely-different-secret").decode()
        with pytest.raises(SignatureMismatch):
            verify(BODY, headers(), secret=other, now=NOW)


class TestNoSecretsLeak:
    def test_the_mismatch_message_carries_no_secret(self, caplog):
        with caplog.at_level("WARNING"), pytest.raises(SignatureMismatch) as exc:
            verify(BODY, headers(), secret="whsec_" + base64.b64encode(b"other").decode(), now=NOW)
        blob = str(exc.value) + caplog.text
        assert "other" not in blob
        assert SECRET not in blob

    def test_the_log_points_at_the_assumption_to_check(self):
        with pytest.raises(SignatureMismatch, match="xai_notes.md A2"):
            verify(BODY, headers(), secret="whsec_" + base64.b64encode(b"other").decode(), now=NOW)
