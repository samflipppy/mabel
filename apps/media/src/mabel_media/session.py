"""The xAI WebSocket relay.

=============================================================================
SAM WRITES THIS FILE. Agents do not implement it.
=============================================================================

04-REPO.md: "media/session.py is Sam's — the real-time path against a sparsely
documented API is where an agent writes confident, wrong code."

What is below is the interface and the TODOs, so everything around it can be
built and tested against a stable shape. The pieces this file needs are all
finished and tested, and none of them are guesses:

  - `config_builder.build_session_update()` — the session.update payload
  - `config_builder.build_opening_disclosure()` — the force_message
  - `config_builder.assert_session_is_safe()` — the last check before sending
  - `prompt.render_prompt()` — the instructions
  - `mabel_xai.client.join_url()` — the socket URL, model pinned
  - `mabel_mcp.tokens.mint_call_token()` — the tenant-scoped call token
  - `postcall.finalize()` — everything after hangup

Notes from building the rest of the call path, for whoever writes this:

**The tenant is already resolved before this file runs.** The webhook handler
looks the dialed number up and hands the session opener a tenant id. Nothing in
here should ever resolve a tenant, and nothing the model says should reach a
tenant lookup.

**The token expires before a long call does.** Fifteen-minute TTL against a
120-minute maximum session. `CallToken.needs_refresh()` is the signal; the
alternative is discovering the expiry when a tool call fails at minute sixteen,
mid-sentence.

**Do not send `response.create` after the opening disclosure.** The force
message is the turn. Sending one makes her say the disclosure and then talk
over herself.

**Archive on hangup, always.** xAI's resumption cache drops history after about
thirty minutes idle and is not a store. `postcall.finalize()` does the work; it
needs to be called even when the call ended badly.

**Fail safe.** If this process cannot answer, the call should fall through to
the carrier's voicemail. A Mabel outage means the contractor is back where he
started, not worse off. Prefer refusing the session to answering badly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class IncomingCall:
    """What the verified `realtime.call.incoming` webhook resolved to.

    Built by the webhook handler, which has already verified the signature and
    looked the dialed number up. By the time it reaches this file, the tenant
    is a fact.
    """

    call_id: str
    tenant_id: UUID
    location_id: UUID | None
    from_e164: str | None
    to_e164: str
    received_at: datetime


class SessionTransport(Protocol):
    """The WebSocket, behind a seam.

    Tests bind `FakeSessionTransport` and never open a socket. The production
    implementation is Sam's.
    """

    async def send(self, payload: dict[str, Any]) -> None: ...

    async def receive(self) -> dict[str, Any]: ...

    async def close(self) -> None: ...


@dataclass
class FakeSessionTransport:
    """What tests bind. Records what was sent, replays a script.

    Lives here rather than in the test tree because the simulation harness
    (Phase 7) drives whole recorded calls through it.
    """

    scripted: list[dict[str, Any]] | None = None
    sent: list[dict[str, Any]] | None = None
    closed: bool = False

    def __post_init__(self) -> None:
        self.scripted = list(self.scripted or [])
        self.sent = []

    async def send(self, payload: dict[str, Any]) -> None:
        assert self.sent is not None
        self.sent.append(payload)

    async def receive(self) -> dict[str, Any]:
        assert self.scripted is not None
        if not self.scripted:
            return {"type": "session.closed"}
        return self.scripted.pop(0)

    async def close(self) -> None:
        self.closed = True

    def sent_of_type(self, message_type: str) -> list[dict[str, Any]]:
        assert self.sent is not None
        return [m for m in self.sent if m.get("type") == message_type]


async def open_session(call: IncomingCall, transport: SessionTransport) -> None:
    """Drive one call from answer to hangup.

    TODO(sam): open `wss://api.x.ai/v1/realtime?call_id=...` with the API key.
      `join_url()` builds it. Ephemeral client secrets are not supported for
      SIP call_id sessions.

    TODO(sam): load the live agent config and knowledge for `call.tenant_id`
      through `tenant_scope`, render the prompt, build the session.update, run
      `assert_session_is_safe()` on it, and send it.

    TODO(sam): send the opening disclosure as a force_message. No
      `response.create` afterwards.

    TODO(sam): pump audio. This is the part that must not share an event loop
      with the portal API, which is why `media` is its own process group.

    TODO(sam): refresh the MCP token when `needs_refresh()` goes true, rather
      than waiting for a tool call to fail.

    TODO(sam): watch concurrency. Ten sessions per team is the ceiling;
      `concurrency_state()` returns "alert" at seven.

    TODO(sam): on hangup — including an abnormal one — collect the turns
      observed live and hand them to `postcall.finalize()`. Do not rely on
      being able to fetch the transcript back from xAI: that route is
      assumption A8 and is unconfirmed.

    TODO(sam): if anything here fails before the session is established, close
      cleanly and let the call fall through to carrier voicemail. Do not answer
      badly.
    """
    raise NotImplementedError(
        "apps/media/src/mabel_media/session.py is Sam's. See 04-REPO.md. "
        "The config builder, prompt renderer, token minting and post-call "
        "handler it needs are all finished and tested."
    )
