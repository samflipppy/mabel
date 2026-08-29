"""The xAI client's guardrails and the cost arithmetic.

None of these open a socket. The point of most of them is that they *cannot*.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

from mabel_xai.client import (
    ALLOWED_TOOLS,
    AUDIO_FORMAT,
    AUDIO_RATE,
    FORBIDDEN_MODEL_ALIAS,
    MAX_CONCURRENT_SESSIONS,
    VOICE_MODEL,
    FakeXaiClient,
    VoiceAgentTemplate,
    XaiClient,
    XaiRefusedUnderTest,
    XaiUnavailable,
    api_key,
    concurrency_state,
    join_url,
    sip_uri,
)
from mabel_xai.pricing import (
    PricingError,
    call_cost_cents,
    conversation_item_cost_cents,
    minutes_from_seconds,
    voice_cost_cents,
)

REPO = Path(__file__).resolve().parents[2]
XAI_PACKAGE = REPO / "packages" / "xai" / "mabel_xai"


def _assigns_to(tree: ast.AST, target: ast.Constant, name: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is target:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                return True
    return False


class TestFailsClosed:
    def test_no_key_means_no_client(self, monkeypatch):
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        with pytest.raises(XaiUnavailable, match="BLOCKED"):
            api_key()

    def test_the_client_refuses_to_run_under_pytest(self, monkeypatch):
        """A test that reaches the live API costs money, risks a key in CI
        logs, and passes for reasons unrelated to the code."""
        monkeypatch.setenv("XAI_API_KEY", "would-not-be-used-anyway")
        with pytest.raises(XaiRefusedUnderTest, match="FakeXaiClient"):
            XaiClient()

    def test_the_error_says_what_is_missing_and_where_to_look(self, monkeypatch):
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        with pytest.raises(XaiUnavailable) as exc:
            api_key()
        assert "XAI_API_KEY" in str(exc.value)
        assert "BLOCKED.md" in str(exc.value)


class TestTheModelIsPinned:
    def test_the_pin(self):
        assert VOICE_MODEL == "grok-voice-think-fast-2.0"

    def test_the_alias_is_never_used(self):
        """`grok-voice-latest` moved 1.0 -> 2.0 on 2026-08-05 and took the
        per-minute price from $0.05 to $0.08 with it. It appears in this
        package exactly once, as the constant naming what not to use."""
        # Structural, not textual: prose about the alias is fine and useful.
        # What must not exist is the alias as a value we could send.
        uses: list[str] = []
        for path in XAI_PACKAGE.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Constant) and node.value == FORBIDDEN_MODEL_ALIAS):
                    continue
                # The one permitted occurrence: the constant that names it so a
                # grep for the alias lands on the comment explaining why not.
                if _assigns_to(tree, node, "FORBIDDEN_MODEL_ALIAS"):
                    continue
                uses.append(f"{path.name}:{node.lineno}")
        assert not uses, f"the grok-voice-latest alias is used as a value at {uses}"

    def test_the_join_url_carries_the_pinned_model(self):
        url = join_url("call_abc")
        assert "call_id=call_abc" in url
        assert VOICE_MODEL in url
        assert url.startswith("wss://api.x.ai/v1/realtime")


class TestTheToolList:
    def test_it_is_the_nine_from_03_voice(self):
        assert ALLOWED_TOOLS == (
            "lookup_customer",
            "get_service_area",
            "check_availability",
            "create_lead",
            "escalate_emergency",
            "book_estimate",
            "get_job_history",
            "answer_question",
            "log_note",
        )

    @pytest.mark.parametrize("banned", ["web_search", "x_search", "file_search"])
    def test_the_expensive_ones_are_absent(self, banned):
        # $5/1k calls, and she starts answering from the open internet.
        assert banned not in ALLOWED_TOOLS

    def test_the_agent_template_ships_exactly_those_tools(self):
        template = VoiceAgentTemplate(
            business_name="Ruiz Plumbing",
            instructions="You are Mabel.",
            mcp_url="https://api.hiremabel.com/mcp",
        )
        payload = template.payload()
        assert payload["model"] == VOICE_MODEL
        assert len(payload["tools"]) == 1
        tool = payload["tools"][0]
        assert tool["type"] == "mcp"
        assert tool["allowed_tools"] == list(ALLOWED_TOOLS)

    def test_the_template_carries_no_voice_clone(self):
        payload = VoiceAgentTemplate(
            business_name="Ruiz Plumbing", instructions="x", mcp_url="https://x"
        ).payload()
        assert "voice_clone" not in payload
        assert "clone" not in str(payload).lower()


class TestSip:
    def test_the_uri_shape(self):
        assert sip_uri("+12165550148") == "sip:+12165550148@sip.voice.x.ai;transport=tls"

    def test_transport_is_tls(self):
        assert "transport=tls" in sip_uri("+12165550148")


class TestAudio:
    def test_mulaw_at_8k_both_directions(self):
        # G.711 mu-law, no transcoding. Anything else adds latency to a
        # conversation that is already latency-sensitive.
        assert AUDIO_FORMAT == "audio/pcmu"
        assert AUDIO_RATE == 8000


class TestConcurrency:
    def test_the_ceiling_is_ten(self):
        assert MAX_CONCURRENT_SESSIONS == 10

    @pytest.mark.parametrize(
        ("active", "expected"),
        [(0, "ok"), (6, "ok"), (7, "alert"), (9, "alert"), (10, "at_limit"), (14, "at_limit")],
    )
    def test_the_alert_fires_before_the_limit(self, active, expected):
        # At the limit the eleventh caller does not reach Mabel at all, so the
        # alert has to leave time to ask xAI for a raise.
        assert concurrency_state(active) == expected


class TestFake:
    async def test_it_records_what_was_asked_of_it(self):
        fake = FakeXaiClient()
        template = VoiceAgentTemplate(
            business_name="Ruiz Plumbing", instructions="x", mcp_url="https://x"
        )
        assert await fake.create_voice_agent(template) == "agent_fake"
        assert fake.calls[0][0] == "create_voice_agent"

    async def test_it_can_report_no_agent_id(self):
        """Assumption A1: if the create route is not what we think, onboarding
        gets None, leaves xai_agent_id NULL, and the shop still drafts."""
        fake = FakeXaiClient(agent_id=None)
        template = VoiceAgentTemplate(
            business_name="Ruiz Plumbing", instructions="x", mcp_url="https://x"
        )
        assert await fake.create_voice_agent(template) is None


class TestPricing:
    def test_a_typical_call(self):
        # Three minutes at $0.08/min is 24 cents, plus the opening disclosure.
        assert voice_cost_cents(180) == 24

    def test_partial_minutes_round_up(self):
        # ASSUMPTION A9: the rate is published, the rounding is not. Up is the
        # direction that cannot understate our own costs.
        assert voice_cost_cents(1) == 1
        assert voice_cost_cents(61) == 9

    def test_a_zero_length_call_costs_nothing(self):
        assert voice_cost_cents(0) == 0

    def test_the_result_is_always_an_integer(self):
        for seconds in (0, 1, 7, 59, 60, 61, 3600):
            assert isinstance(voice_cost_cents(seconds), int)

    def test_floats_are_refused(self):
        # A float in a money path is the bug this repo greps for.
        with pytest.raises(PricingError, match="whole seconds"):
            voice_cost_cents(180.5)  # type: ignore[arg-type]

    def test_bools_are_refused(self):
        with pytest.raises(PricingError):
            voice_cost_cents(True)  # type: ignore[arg-type]

    def test_negative_durations_are_refused(self):
        with pytest.raises(PricingError, match="negative"):
            voice_cost_cents(-1)

    def test_conversation_items_are_charged(self):
        assert conversation_item_cost_cents(0) == 0
        assert conversation_item_cost_cents(1) == 1  # $0.004, rounded up to a cent

    def test_a_full_call_totals_both_meters(self):
        assert call_cost_cents(duration_sec=180, conversation_items=1) == 25

    def test_minutes_are_minutes_not_money(self):
        # usage_daily.voice_minutes is numeric(10,2). Minutes, not dollars.
        assert minutes_from_seconds(90) == 1.5
        assert minutes_from_seconds(100) == 1.67


class TestNoCredentialsInTheRepo:
    def test_no_key_shaped_literal_is_committed(self):
        """We never hold a tenant's credentials, and we never commit our own.
        `xai-` prefixed literals are what an xAI key looks like."""
        pattern = re.compile(r"xai-[A-Za-z0-9]{16,}")
        for path in XAI_PACKAGE.rglob("*.py"):
            body = path.read_text(encoding="utf-8")
            assert not pattern.search(body), f"{path.name} may contain an API key"

    def test_the_key_is_only_ever_read_from_the_environment(self):
        client = (XAI_PACKAGE / "client.py").read_text(encoding="utf-8")
        assert 'os.environ.get("XAI_API_KEY")' in client
        # And there is no default. `os.environ.get("XAI_API_KEY", "something")`
        # would be a stubbed credential wearing a disguise.
        assert 'os.environ.get("XAI_API_KEY",' not in client

    def test_no_env_file_is_committed_next_to_the_package(self):
        assert not list(XAI_PACKAGE.parent.glob(".env*"))


class TestAssumptionsAreLabelled:
    def test_every_assumption_in_the_code_points_at_the_ledger(self):
        """docs/xai_notes.md is the ledger. An assumption in the code that is
        not in the table is one nobody will think to check against a live
        response."""
        notes = (REPO / "docs" / "xai_notes.md").read_text(encoding="utf-8")
        referenced = set()
        for path in XAI_PACKAGE.rglob("*.py"):
            referenced.update(
                re.findall(
                    r"ASSUMPTION \((?:docs/xai_notes\.md )?(A\d+)\)",
                    path.read_text(encoding="utf-8"),
                )
            )
        assert referenced, "expected the client to label its assumptions"
        for marker in sorted(referenced):
            assert f"| {marker} |" in notes, (
                f"{marker} is marked in the code but has no row in docs/xai_notes.md"
            )

    def test_the_notes_file_records_the_tool_count_decision(self):
        notes = (REPO / "docs" / "xai_notes.md").read_text(encoding="utf-8")
        assert "Tool-count conflict" in notes
        assert "answer_question" in notes


def test_no_live_key_is_present_in_this_environment():
    """A canary. If a real key ever ends up in the test environment, the
    refuse-under-pytest guard is the only thing between it and the API."""
    key = os.environ.get("XAI_API_KEY", "")
    assert not key.startswith("xai-"), "a live-looking XAI_API_KEY is set while tests run"
