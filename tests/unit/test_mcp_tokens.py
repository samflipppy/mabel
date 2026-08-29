"""The call token. Everything about which tenant a tool call may touch rests
on this file being right, so it gets the adversarial treatment."""

from __future__ import annotations

import time
from uuid import UUID, uuid4

import jwt
import pytest

from mabel_mcp.tokens import (
    ALGORITHM,
    AUDIENCE,
    ISSUER,
    REFRESH_BELOW_SECONDS,
    TTL_SECONDS,
    SigningKeyUnavailable,
    TokenError,
    bearer,
    from_authorization_header,
    mint_call_token,
    signing_key,
    verify_call_token,
)

KEY = "a-test-signing-key-long-enough-to-be-accepted"
TENANT = UUID("11111111-1111-1111-1111-111111111111")
OTHER_TENANT = UUID("22222222-2222-2222-2222-222222222222")
CALL = "call_abc123"
NOW = 1_800_000_000.0


class TestMinting:
    def test_a_minted_token_verifies(self):
        token = mint_call_token(TENANT, CALL, key=KEY, now=NOW)
        claims = verify_call_token(token, key=KEY, now=NOW)
        assert claims.tenant_id == TENANT
        assert claims.call_id == CALL

    def test_a_string_uuid_is_accepted(self):
        token = mint_call_token(str(TENANT), CALL, key=KEY, now=NOW)
        assert verify_call_token(token, key=KEY, now=NOW).tenant_id == TENANT

    @pytest.mark.parametrize("bad", ["not-a-uuid", "", None, 12345, "'; DROP TABLE leads; --"])
    def test_a_non_uuid_tenant_is_refused_at_minting(self, bad):
        # The same guard tenant_scope applies, for the same reason: this value
        # decides which rows a handler can see.
        with pytest.raises(TokenError, match="UUID"):
            mint_call_token(bad, CALL, key=KEY, now=NOW)

    @pytest.mark.parametrize("bad", ["", None, 123])
    def test_a_missing_call_id_is_refused(self, bad):
        with pytest.raises(TokenError, match="call_id"):
            mint_call_token(TENANT, bad, key=KEY, now=NOW)

    def test_the_ttl_is_fifteen_minutes(self):
        token = mint_call_token(TENANT, CALL, key=KEY, now=NOW)
        claims = verify_call_token(token, key=KEY, now=NOW)
        assert claims.expires_at - claims.issued_at == TTL_SECONDS == 900


class TestForgery:
    def test_a_token_signed_with_another_key_is_refused(self):
        forged = mint_call_token(
            OTHER_TENANT, CALL, key="a-different-key-also-long-enough!", now=NOW
        )
        with pytest.raises(TokenError):
            verify_call_token(forged, key=KEY, now=NOW)

    def test_the_alg_none_attack_is_refused(self):
        """`{"alg": "none"}` lets anyone mint a token for any tenant. Passing
        an explicit algorithm list to jwt.decode is what stops it."""
        payload = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": str(OTHER_TENANT),
            "tenant_id": str(OTHER_TENANT),
            "call_id": CALL,
            "iat": int(NOW),
            "exp": int(NOW) + 900,
        }
        unsigned = jwt.encode(payload, key="", algorithm="none")
        with pytest.raises(TokenError):
            verify_call_token(unsigned, key=KEY, now=NOW)

    def test_a_token_for_another_audience_is_refused(self):
        payload = {
            "iss": ISSUER,
            "aud": "some-other-service",
            "sub": str(TENANT),
            "tenant_id": str(TENANT),
            "call_id": CALL,
            "iat": int(NOW),
            "exp": int(NOW) + 900,
        }
        token = jwt.encode(payload, KEY, algorithm=ALGORITHM)
        with pytest.raises(TokenError, match="MCP server"):
            verify_call_token(token, key=KEY, now=NOW)

    def test_a_token_from_another_issuer_is_refused(self):
        payload = {
            "iss": "somebody-else",
            "aud": AUDIENCE,
            "sub": str(TENANT),
            "tenant_id": str(TENANT),
            "call_id": CALL,
            "iat": int(NOW),
            "exp": int(NOW) + 900,
        }
        token = jwt.encode(payload, KEY, algorithm=ALGORITHM)
        with pytest.raises(TokenError, match="minted by us"):
            verify_call_token(token, key=KEY, now=NOW)

    @pytest.mark.parametrize("claim", ["tenant_id", "call_id", "exp", "iat"])
    def test_a_token_missing_a_required_claim_is_refused(self, claim):
        payload = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": str(TENANT),
            "tenant_id": str(TENANT),
            "call_id": CALL,
            "iat": int(NOW),
            "exp": int(NOW) + 900,
        }
        del payload[claim]
        token = jwt.encode(payload, KEY, algorithm=ALGORITHM)
        with pytest.raises(TokenError):
            verify_call_token(token, key=KEY, now=NOW)

    def test_a_token_with_a_non_uuid_tenant_is_refused(self):
        payload = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "x",
            "tenant_id": "all",
            "call_id": CALL,
            "iat": int(NOW),
            "exp": int(NOW) + 900,
        }
        token = jwt.encode(payload, KEY, algorithm=ALGORITHM)
        with pytest.raises(TokenError, match="tenant_id"):
            verify_call_token(token, key=KEY, now=NOW)

    def test_garbage_is_refused(self):
        for junk in ["", "not.a.token", "a.b.c", "Bearer something"]:
            with pytest.raises(TokenError):
                verify_call_token(junk, key=KEY, now=NOW)

    def test_a_tampered_payload_is_refused(self):
        token = mint_call_token(TENANT, CALL, key=KEY, now=NOW)
        header, payload, signature = token.split(".")
        forged = mint_call_token(OTHER_TENANT, CALL, key=KEY, now=NOW)
        swapped = ".".join([header, forged.split(".")[1], signature])
        with pytest.raises(TokenError):
            verify_call_token(swapped, key=KEY, now=NOW)


class TestExpiry:
    def test_an_expired_token_is_refused(self):
        token = mint_call_token(TENANT, CALL, key=KEY, now=NOW)
        with pytest.raises(TokenError, match="expired"):
            verify_call_token(token, key=KEY, now=NOW + TTL_SECONDS + 120)

    def test_a_token_inside_its_window_is_fine(self):
        token = mint_call_token(TENANT, CALL, key=KEY, now=NOW)
        assert verify_call_token(token, key=KEY, now=NOW + 600).tenant_id == TENANT

    def test_remaining_seconds_counts_down(self):
        token = mint_call_token(TENANT, CALL, key=KEY, now=NOW)
        claims = verify_call_token(token, key=KEY, now=NOW)
        assert claims.remaining_seconds(now=NOW) == TTL_SECONDS
        assert claims.remaining_seconds(now=NOW + 300) == TTL_SECONDS - 300

    def test_a_long_call_is_told_to_refresh_before_a_tool_fails(self):
        """A 120-minute maximum session against a 15-minute token means a long
        call outlives it. The media process refreshes on this signal rather
        than finding out when a tool call fails at minute sixteen."""
        token = mint_call_token(TENANT, CALL, key=KEY, now=NOW)
        claims = verify_call_token(token, key=KEY, now=NOW)
        assert claims.needs_refresh(now=NOW) is False
        assert claims.needs_refresh(now=NOW + TTL_SECONDS - REFRESH_BELOW_SECONDS + 1) is True


class TestFailsClosed:
    def test_no_signing_key_means_no_token(self, monkeypatch):
        monkeypatch.delenv("MCP_TOKEN_SIGNING_KEY", raising=False)
        with pytest.raises(SigningKeyUnavailable, match="BLOCKED"):
            signing_key()

    def test_a_short_key_is_refused(self, monkeypatch):
        # What this HMAC protects is cross-tenant access to call data.
        monkeypatch.setenv("MCP_TOKEN_SIGNING_KEY", "tooshort")
        with pytest.raises(SigningKeyUnavailable, match="32"):
            signing_key()

    def test_minting_without_a_key_fails_rather_than_defaulting(self, monkeypatch):
        monkeypatch.delenv("MCP_TOKEN_SIGNING_KEY", raising=False)
        with pytest.raises(SigningKeyUnavailable):
            mint_call_token(TENANT, CALL)

    def test_verifying_without_a_key_fails_closed(self, monkeypatch):
        monkeypatch.delenv("MCP_TOKEN_SIGNING_KEY", raising=False)
        token = mint_call_token(TENANT, CALL, key=KEY, now=NOW)
        with pytest.raises(SigningKeyUnavailable):
            verify_call_token(token)


class TestHeaderHandling:
    def test_the_bearer_form(self):
        assert bearer("abc").startswith("Bearer ")

    def test_extracting_from_a_header(self):
        assert from_authorization_header("Bearer abc") == "abc"

    def test_the_scheme_is_case_insensitive(self):
        assert from_authorization_header("bearer abc") == "abc"

    @pytest.mark.parametrize("bad", [None, "", "abc", "Basic abc", "Bearer", "Bearer   "])
    def test_anything_else_is_refused(self, bad):
        with pytest.raises(TokenError):
            from_authorization_header(bad)


class TestRoundTripWithRealClock:
    def test_a_freshly_minted_token_verifies_against_the_real_clock(self):
        """Everything else pins `now`. This one does not, so a sign error in
        the expiry arithmetic cannot hide behind a fixed timestamp."""
        token = mint_call_token(TENANT, CALL, key=KEY)
        claims = verify_call_token(token, key=KEY)
        assert claims.tenant_id == TENANT
        assert 0 < claims.remaining_seconds() <= TTL_SECONDS
        assert claims.issued_at <= int(time.time()) + 1


class TestTheTokenIsTheOnlyTenantSource:
    def test_two_calls_get_two_tenants(self):
        one = verify_call_token(
            mint_call_token(TENANT, "call_1", key=KEY, now=NOW), key=KEY, now=NOW
        )
        two = verify_call_token(
            mint_call_token(OTHER_TENANT, "call_2", key=KEY, now=NOW), key=KEY, now=NOW
        )
        assert one.tenant_id != two.tenant_id

    def test_a_token_binds_to_one_call(self):
        """The call id is in the token so a token lifted from one call cannot
        be replayed to write into another one's records."""
        claims = verify_call_token(
            mint_call_token(TENANT, "call_1", key=KEY, now=NOW), key=KEY, now=NOW
        )
        assert claims.call_id == "call_1"


def test_a_uuid_is_returned_not_a_string():
    """A string here would flow into tenant_scope, which validates again — but
    typing it as UUID means the compiler catches a handler that passes a raw
    argument through instead."""
    claims = verify_call_token(mint_call_token(TENANT, CALL, key=KEY, now=NOW), key=KEY, now=NOW)
    assert isinstance(claims.tenant_id, UUID)
    assert claims.tenant_id != uuid4()
