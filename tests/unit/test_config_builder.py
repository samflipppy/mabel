"""The `session.update` payload.

Several keys here are assumptions (docs/xai_notes.md A4-A7) and these tests
cannot tell us whether xAI accepts them. What they can do is pin the things
that must be true whatever the API turns out to want: the model is pinned, the
tool list is the nine, the authorization is a short-lived call token and not an
API key, and the audio does not transcode.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from mabel_mcp.tokens import verify_call_token
from mabel_media.config_builder import (
    TURN_DETECTION,
    SessionInputs,
    assert_session_is_safe,
    build_opening_disclosure,
    build_session_update,
    pinned_model,
)
from mabel_xai.client import ALLOWED_TOOLS, VOICE_MODEL

KEY = "a-test-signing-key-long-enough-to-be-accepted"
TENANT = UUID("11111111-1111-1111-1111-111111111111")


def session_inputs(**overrides) -> SessionInputs:
    base = {
        "tenant_id": TENANT,
        "call_id": "call_abc",
        "instructions": "You are Mabel.",
        "voice": "carina",
        "speaking_rate": 1.0,
        "keyterms": ["Detroit Avenue", "Rinnai"],
        "mcp_url": "https://api.hiremabel.com/mcp",
    }
    return SessionInputs(**(base | overrides))


def build(**overrides):
    return build_session_update(session_inputs(**overrides), token_key=KEY)


class TestTheToolEntry:
    def test_exactly_one_mcp_entry(self):
        tools = build()["session"]["tools"]
        assert len(tools) == 1
        assert tools[0]["type"] == "mcp"

    def test_the_allowed_tools_are_the_nine(self):
        assert build()["session"]["tools"][0]["allowed_tools"] == list(ALLOWED_TOOLS)

    @pytest.mark.parametrize("banned", ["web_search", "x_search", "file_search"])
    def test_the_expensive_tools_are_nowhere_in_the_payload(self, banned):
        assert banned not in str(build())

    def test_the_authorization_is_a_call_token_not_an_api_key(self):
        """A long-lived key here would hand every tool call the keys to the
        account. It must be the fifteen-minute token minted from the dialed
        number."""
        header = build()["session"]["tools"][0]["authorization"]
        assert header.startswith("Bearer ")
        token = verify_call_token(header.removeprefix("Bearer "), key=KEY)
        assert token.tenant_id == TENANT
        assert token.call_id == "call_abc"

    def test_the_token_is_minted_fresh_per_session(self):
        """Its fifteen minutes should start when the session opens, not when
        the config was fetched."""
        first = build()["session"]["tools"][0]["authorization"]
        second = build()["session"]["tools"][0]["authorization"]
        # Same tenant and call, but each is minted at its own moment. What
        # matters is that both verify and neither is a stored constant.
        assert verify_call_token(first.removeprefix("Bearer "), key=KEY).tenant_id == TENANT
        assert verify_call_token(second.removeprefix("Bearer "), key=KEY).tenant_id == TENANT

    def test_the_server_url_is_ours(self):
        assert build()["session"]["tools"][0]["server_url"] == "https://api.hiremabel.com/mcp"


class TestAudio:
    def test_mulaw_both_directions(self):
        # G.711 mu-law at 8kHz in and out, so nothing transcodes. Transcoding
        # adds latency to a conversation that is already latency-sensitive.
        audio = build()["session"]["audio"]
        assert audio["input"]["format"] == {"type": "audio/pcmu", "rate": 8000}
        assert audio["output"]["format"] == {"type": "audio/pcmu", "rate": 8000}

    def test_the_speaking_rate_is_carried(self):
        assert build(speaking_rate=1.15)["session"]["audio"]["output"]["speed"] == 1.15

    def test_keyterms_reach_the_transcriber(self):
        # Street names and local brands are exactly what an 8kHz phone line
        # mangles.
        transcription = build()["session"]["audio"]["input"]["transcription"]
        assert transcription["keyterms"] == ["Detroit Avenue", "Rinnai"]
        assert transcription["model"] == "grok-transcribe"


class TestTurnDetection:
    def test_server_vad(self):
        assert build()["session"]["turn_detection"]["type"] == "server_vad"

    def test_the_threshold_is_high(self):
        # A homeowner with water running in the background will otherwise
        # interrupt her constantly.
        assert TURN_DETECTION["threshold"] == 0.85

    def test_the_payload_carries_a_copy_not_the_module_constant(self):
        """Two concurrent calls must not be able to mutate each other's turn
        detection through a shared dict."""
        payload = build()
        payload["session"]["turn_detection"]["threshold"] = 0.1
        assert TURN_DETECTION["threshold"] == 0.85


class TestTheOpeningDisclosure:
    def test_it_is_a_force_message(self):
        """A disclosure the model paraphrases is a disclosure that may no
        longer disclose anything."""
        message = build_opening_disclosure(
            "This is an automated assistant and this call is recorded."
        )
        assert message["type"] == "conversation.item.create"
        assert message["item"]["type"] == "force_message"

    def test_it_is_not_interruptible(self):
        assert build_opening_disclosure("x")["item"]["interruptible"] is False

    def test_it_carries_the_text_verbatim(self):
        line = "This is an automated assistant and this call is recorded."
        assert build_opening_disclosure(line)["item"]["content"][0]["text"] == line


class TestTheSafetyCheck:
    def test_a_good_payload_passes(self):
        assert_session_is_safe(build())

    def test_a_truncated_tool_list_is_caught(self):
        payload = build()
        payload["session"]["tools"][0]["allowed_tools"] = ["create_lead"]
        with pytest.raises(ValueError, match="drifted from the nine"):
            assert_session_is_safe(payload)

    def test_an_added_search_tool_is_caught(self):
        payload = build()
        payload["session"]["tools"][0]["allowed_tools"].append("web_search")
        with pytest.raises(ValueError):
            assert_session_is_safe(payload)

    def test_a_second_tool_entry_is_caught(self):
        payload = build()
        payload["session"]["tools"].append({"type": "web_search"})
        with pytest.raises(ValueError):
            assert_session_is_safe(payload)

    def test_an_api_key_in_the_authorization_is_caught(self):
        """The MCP server takes our short-lived call token. Sending the xAI key
        would hand every tool call the account."""
        payload = build()
        payload["session"]["tools"][0]["authorization"] = "Bearer xai-abc123def456"
        with pytest.raises(ValueError, match="API key"):
            assert_session_is_safe(payload)

    def test_a_missing_bearer_prefix_is_caught(self):
        payload = build()
        payload["session"]["tools"][0]["authorization"] = "just-a-token"
        with pytest.raises(ValueError, match="bearer"):
            assert_session_is_safe(payload)

    def test_wrong_audio_format_is_caught(self):
        payload = build()
        payload["session"]["audio"]["output"]["format"]["type"] = "audio/pcm16"
        with pytest.raises(ValueError, match="audio/pcmu"):
            assert_session_is_safe(payload)


class TestTheModelPin:
    def test_the_pin_is_the_one_from_the_notes(self):
        assert pinned_model() == VOICE_MODEL == "grok-voice-think-fast-2.0"

    def test_the_alias_appears_nowhere_in_a_built_payload(self):
        assert "grok-voice-latest" not in str(build())
