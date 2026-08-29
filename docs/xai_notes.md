# xAI API — verified vs. assumed

`packages/xai/client.py` is the only file that talks to xAI. This is the file
that says what we actually know. The API is sparsely documented; if you guess a
parameter name you will write confident, wrong code.

**The rule:** anything not marked VERIFIED below is marked `# ASSUMPTION:` at
its use site in `client.py` and appears in the Assumptions table here. If you
add an assumption to the code, add the row. If you confirm one against a live
response, move it up and cite what confirmed it.

Companion doc: [`xai-voice.md`](xai-voice.md) — the product-facing narrative.
This file is the API contract ledger.

---

## VERIFIED

Sourced 2026-08-29 from docs.x.ai — [speech to speech](https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech),
[SIP](https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech/sip),
[pricing](https://docs.x.ai/developers/pricing),
[voice REST reference](https://docs.x.ai/developers/rest-api-reference/inference/voice).

| Fact | Value |
|---|---|
| Model, pinned | `grok-voice-think-fast-2.0`. Never `grok-voice-latest` — that alias moved 1.0 → 2.0 on 2026-08-05 and took the price with it, $0.05 → $0.08/min. |
| Realtime endpoint | `wss://api.x.ai/v1/realtime` |
| SIP join | `wss://api.x.ai/v1/realtime?call_id={call_id}`, authenticated with the xAI **API key**. Ephemeral client secrets are **not** supported for SIP `call_id` sessions. |
| `model` on a SIP session | **Ignored.** The session binds to the inbound call. We pin it anyway for direct (non-SIP) sessions. |
| SIP FQDN | `sip.voice.x.ai`, port 5060, A record. URI `sip:{number}@sip.voice.x.ai;transport=tls`. Origin `byo_trunk`. |
| Codec | G.711. We speak μ-law: `audio/pcmu` at 8000 Hz, in and out. No transcoding. |
| Inbound webhook event | `realtime.call.incoming` |
| Webhook headers | Standard Webhooks: `webhook-id`, `webhook-timestamp`, `webhook-signature` |
| Turn detection | `server_vad` |
| MCP transport | Streamable HTTP or SSE. **stdio is not supported.** |
| Concurrency | 10 concurrent sessions per team (default) |
| Max session | 120 minutes |
| Retention | Session resumption cache drops history after ~30 min idle. Not a store. Archive post-call, always. |
| Price — speech-to-speech | $0.08/min for `grok-voice-think-fast-2.0` |
| Price — extra `conversation.item.create` text | $0.004 |
| Price — `web_search` / `x_search` | $5 / 1k calls. **We never enable either.** |

### Opening disclosure — VERIFIED shape

`conversation.item.create` with `item.type` = `force_message` and
`interruptible` = `false`. Do **not** follow it with `response.create` — the
force message *is* the turn.

> This is an automated assistant and this call is recorded.

---

## ASSUMPTIONS

Every row here is `# ASSUMPTION:` in the code. None of it is confirmed against
a live response.

| # | Assumption | Where | Why we think so | How to confirm |
|---|---|---|---|---|
| A1 | Voice Agents are created with `POST https://api.x.ai/v1/voice-agents` | `xai/client.py::create_voice_agent` | Public docs (2026-08-29) list no create route. This is the most likely shape by convention with the rest of `/v1`. | Mint one agent by hand in console.x.ai, watch the network tab. If the route differs, onboarding leaves `tenants.xai_agent_id` NULL and the shop still drafts — nothing breaks. |
| A2 | The webhook signature is HMAC-SHA256 over `{webhook-id}.{webhook-timestamp}.{raw_body}`, base64, prefixed `v1,` | `xai/webhooks.py` | This is the Standard Webhooks spec, and xAI sends the Standard Webhooks header trio. | First real inbound webhook. `verify()` logs the computed and received digests side by side on mismatch (never the secret). |
| A3 | The signing secret is base64 after a `whsec_` prefix, as Standard Webhooks specifies | `xai/webhooks.py::_decode_secret` | Same. We accept both raw and `whsec_`-prefixed and try both encodings. | Same as A2. |
| A4 | `session.update` accepts `audio.input.transcription.keyterms` as a list of strings | `media/config_builder.py` | Named in 03-VOICE.md's session payload; not in the public reference we read. | Send it; a strict server would reject the whole `session.update`. Config builder can drop keyterms behind `XAI_SEND_KEYTERMS=0` if it does. |
| A5 | `turn_detection` accepts `threshold`, `silence_duration_ms`, `prefix_padding_ms`, `idle_timeout_ms` | `media/config_builder.py` | From 03-VOICE.md. Names match the OpenAI realtime convention xAI otherwise tracks. | Same. |
| A6 | `audio.output.speed` is the speaking-rate knob and takes a float around 1.0 | `media/config_builder.py` | From 03-VOICE.md. | Same. |
| A7 | The MCP tool entry takes `{type:"mcp", server_url, server_label, authorization, allowed_tools}` | `media/config_builder.py` | From 03-VOICE.md. | Same. |
| A8 | Recording and full transcript are retrievable post-call by `call_id` | `xai/client.py::fetch_recording`, `fetch_transcript` | Required by invariant 7; the route is not documented. | First archived call. Until confirmed, `postcall` also reconstructs the transcript from the turns the media process observed live, so we are never dependent on this route. |
| A9 | Per-minute billing is wall-clock audio seconds, rounded up to the second, at $0.08/min | `xai/pricing.py` | Published rate, unpublished rounding. | Reconcile our computed `calls.voice_cost_cents` against the first xAI invoice. Ours is a cost estimate for margin tracking — it is never shown to a customer as a dollar figure. |

---

## Tool-count conflict — resolved 2026-08-29

`03-VOICE.md` lists **nine** entries in `allowed_tools`; `AGENTS.md` and
`xai-voice.md` say **eight tools only**. The difference is `answer_question`.

**Resolved: nine.** `03-VOICE.md` is the voice specification and defines
`answer_question` in full, including its `{found: false}` contract — the tool
that stops Mabel guessing when she has no Q&A pair. The "eight" figure predates
it. The spirit of the invariant is *this closed list and nothing else* — no
`web_search`, no `x_search`, no `file_search` — and that still holds.

The list, and it is exhaustive:

`lookup_customer` · `get_service_area` · `check_availability` · `create_lead` ·
`escalate_emergency` · `book_estimate` · `get_job_history` · `answer_question` ·
`log_note`

---

## Standing rules

- **No key ever lands in this repo.** Not here, not in `.env.example`, not in a
  test fixture. `client.py` refuses to construct without `XAI_API_KEY` and
  refuses outright under pytest.
- **Never enable `web_search`, `x_search`, or `file_search`.** Cost, and she
  starts answering from the open internet.
- **Never put a price sheet in a collection.** Collections may hold non-price
  shop docs only. A PDF with dollar figures in it is how she starts quoting.
- **No LLM output becomes a dollar figure.** `voice_cost_cents` is computed by
  `xai/pricing.py` from a duration and a constant, and it is internal.
