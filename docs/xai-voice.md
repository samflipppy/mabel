# xAI Voice — how Mabel uses it

Researched 2026-08-29 from xAI docs ([speech to speech](https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech), [SIP](https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech/sip), [pricing](https://docs.x.ai/developers/pricing), [voice API](https://docs.x.ai/developers/rest-api-reference/inference/voice)).

This file is the in-repo knowledge base. It is not a live join. This repo still does not open `wss://api.x.ai/v1/realtime`. Sam has to approve that change on that specific change.

No keys belong in this file.

## Pin the model

Pin **`grok-voice-think-fast-2.0`** in code. Never `grok-voice-latest`.

The alias `grok-voice-latest` moved from Think Fast 1.0 to Think Fast 2.0 on **August 5, 2026**. The per-minute price moved with it, **$0.05 → $0.08**. We never use the alias.

## Price card (published)

| Meter | Rate |
| --- | --- |
| Speech-to-speech `grok-voice-think-fast-2.0` | **$0.08 / min** ($4.80 / hr) audio |
| Extra `conversation.item.create` text | **$0.004** |
| Speech-to-speech `grok-voice-think-fast-1.0` (do not use) | $0.05 / min |

Do **not** enable `web_search` or `x_search`. Those are **$5 / 1k** calls. Collections may hold non-price shop docs. Reject dollar-looking uploads the same way greeting notes are rejected. Do not dump a price sheet into a collection.

## SIP (Telnyx)

- Telnyx FQDN: **`sip.voice.x.ai`** (Voice Suite → SIP Trunking → FQDN connection, port 5060, record type A).
- URI: **`sip:{number}@sip.voice.x.ai;transport=tls`**
- Origin: **`byo_trunk`** (customer-owned number).
- Codec: **G.711** (μ-law, A-law, or G.722). We speak **`audio/pcmu`**.
- Inbound number format: E.164.

Webhook event: **`realtime.call.incoming`**. Verify Standard Webhooks headers:

- `webhook-id`
- `webhook-timestamp`
- `webhook-signature`

Tenant comes from the **SIP To DID**, never from anything the model passes.

Join URL (not wired in this repo): `wss://api.x.ai/v1/realtime?call_id={call_id}` with the xAI **API key**. Ephemeral client secrets are **not** supported for SIP `call_id` sessions.

## session.update

After a join that Sam has approved, send `session.update` with:

- `instructions` — shop packet facts injected after DID resolve, plus the vertical rules. No dollar figures.
- Built-in `voice` (for example `eve`). **No cloning.**
- `turn_detection.type`: `server_vad`
- Our eight MCP tools (below), not `web_search`, not `x_search`, not `file_search`
- `audio.input.format.type` and `audio.output.format.type`: **`audio/pcmu`**

## Opening disclosure

Via `conversation.item.create` with `item.type` = `force_message`, `interruptible` = **false**. Do not send `response.create` for this turn — the force message is the turn.

Exact line:

> This is an automated assistant and this call is recorded.

## MCP

Streamable HTTP or SSE only. stdio is not used.

`authorization` on the MCP tool is the **short-lived tenant token we mint after DID resolve**. Tool handlers trust that token, not a `tenant_id` argument.

## Eight tools only

1. `lookup_customer`
2. `get_service_area`
3. `check_availability`
4. `create_lead`
5. `escalate_emergency`
6. `book_estimate`
7. `get_job_history`
8. `log_note`

`allowed_tools` on the MCP server entry must be this list. Nothing else.

## Archive ourselves

xAI session resumption cache drops history after **30 minutes of idle**. Audio is processed in real time and is not our store. Archive transcript and recording to our own storage immediately post-call. Do not treat the xAI cache as storage.

## One agent per shop

Each client gets their own xAI Voice Agent in console.x.ai. Reason: per-shop call logs, collections/docs, and MCP connections (Jobber, Google).

We do **not** click templates like Customer Support per shop. Onboard creates the agent from **our** template:

- She is Mabel. Not a generic support bot.
- Opening disclosure: force_message, not interruptible.
- Never quote a price. Never invent an arrival time.
- Pin **`grok-voice-think-fast-2.0`**. No voice clone.
- No `web_search`. No `x_search`. Eight tools only.

Shop packet (hours, zips, owner SMS) still lives in **our** database (`infra/0002_shop_packet.sql`) and is injected after tenant resolution from the inbound DID. Store `xai_voice_agent_id` on the tenant when we have it (`infra/0003_xai_voice_agent.sql`, draft). Onboard records it as optional/null until we actually create the agent. This repo still does not call the xAI API to create one.

Collections may hold non-price shop docs (hours sheet, service-area notes with no figures). Reject dollar-looking uploads the same way greeting notes are rejected. A price PDF in a collection is how she starts quoting. Don't.
