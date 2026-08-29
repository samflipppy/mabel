# Mabel v2 — Voice Layer

---

## Call path

```
Homeowner dials the contractor's published number
  → carrier conditional forward (no-answer / busy / after-hours)
  → Telnyx DID assigned to this tenant
  → SIP/TLS → sip:{did}@sip.voice.x.ai
  → xAI POSTs signed webhook: realtime.call.incoming
  → media process: verify signature → resolve tenant from To-DID
                  → build session config from live agent_config
                  → open wss://api.x.ai/v1/realtime?call_id={call_id}
                  → session.update, response.create
  → Mabel converses, calls MCP tools mid-call
  → hangup → post-call handler: archive recording + transcript,
             finalize lead, enqueue notifications, compute cost
```

**Tenant resolution is server-side, from the dialed number, before the socket
opens.** Nothing the model says influences which tenant's data it can touch.

---

## Webhook handling

xAI sends `webhook-id`, `webhook-timestamp`, `webhook-signature`.

1. Read the **raw body** — never re-serialize, it breaks the signature
2. Reject if timestamp is older than 300s
3. Verify HMAC against the per-number `dispatch_signing_secret`
4. Insert `webhook_id` into `webhook_receipts`; on conflict, return 200 and stop
5. Resolve tenant by `to` number
6. Hand off to the session opener

---

## Session configuration

Built per call from that tenant's live `agent_configs` row.

```python
{
  "type": "session.update",
  "session": {
    "instructions": render_prompt(tenant, config, knowledge_items),
    "voice": config.voice,
    "audio": {
      "input":  {"format": {"type": "audio/pcmu", "rate": 8000},
                 "transcription": {"model": "grok-transcribe",
                                   "keyterms": config.keyterms}},
      "output": {"format": {"type": "audio/pcmu", "rate": 8000},
                 "speed": config.speaking_rate}
    },
    "turn_detection": {
      "type": "server_vad",
      "threshold": 0.85,
      "silence_duration_ms": 700,
      "prefix_padding_ms": 333,
      "idle_timeout_ms": 8000
    },
    "tools": [{
      "type": "mcp",
      "server_url": "https://api.hiremabel.com/mcp",
      "server_label": "mabel",
      "authorization": f"Bearer {mint_call_token(tenant_id, call_id)}",
      "allowed_tools": ["lookup_customer","get_service_area","check_availability",
                        "create_lead","escalate_emergency","book_estimate",
                        "get_job_history","answer_question","log_note"]
    }]
  }
}
```

The MCP token is a JWT, 15-minute TTL, carrying `tenant_id` and `call_id`. The
MCP server trusts the token, never a tool argument.

---

## Prompt structure

Rendered from config, fixed section order:

**Role** — "You are Mabel, the after-hours assistant for {business}, a {trade}
company in {city}."

**Opening** — always: "you are an automated assistant and this call is
recorded." Delivered via `force_message` so it's verbatim.

**Collect, confirming each back** — name, service address, callback number,
what they need, urgency, how they heard about the business.

**Services** — what they do, what they don't.

**Service area** — ZIPs. If out of area, say so politely and offer to take a
message anyway.

**Emergency criteria** — rendered from the trade ruleset plus tenant overrides.

**Hard rules** — never quote a price, a range, or an hourly rate. Never take
payment info. Never promise an arrival time not returned by `check_availability`.
Always confirm the address by reading it back.

**Knowledge** — the Q&A pairs.

**Close** — confirm the callback number, tell them when someone will reach out.

**Voice** — calm, competent, unhurried. Short sentences. Never oversells.

---

## MCP tools

```json
{"name": "lookup_customer",
 "description": "Check whether this caller is an existing customer.",
 "inputSchema": {"type":"object",
   "properties": {"phone":{"type":"string"},"address":{"type":"string"}},
   "anyOf":[{"required":["phone"]},{"required":["address"]}]}}
```
Returns `{found, name, last_job, last_job_date, open_balance}`. Lets Mabel
open with "Hi Mrs. Henderson — is this about the exterior job?"

```json
{"name": "get_service_area",
 "inputSchema": {"type":"object",
   "properties":{"zip":{"type":"string"},"city":{"type":"string"}},
   "required":["zip"]}}
```
Returns `{in_area, note}`.

```json
{"name": "check_availability",
 "inputSchema": {"type":"object",
   "properties":{"job_type":{"type":"string"},
                 "preferred_window":{"type":"string"}},
   "required":["job_type"]}}
```
Returns real slots from Google Calendar if connected, otherwise the tenant's
configured default windows. **Mabel never invents a time.**

```json
{"name": "create_lead",
 "inputSchema": {"type":"object","properties":{
   "name":{"type":"string"},"phone":{"type":"string"},
   "address":{"type":"string"},"job_type":{"type":"string"},
   "description":{"type":"string"},
   "urgency":{"enum":["routine","soon","emergency"]},
   "source":{"type":"string"}},
   "required":["name","phone","job_type","urgency"]}}
```

```json
{"name": "escalate_emergency",
 "inputSchema": {"type":"object","properties":{
   "name":{"type":"string"},"phone":{"type":"string"},
   "address":{"type":"string"},"nature":{"type":"string"},
   "caller_is_safe":{"type":"boolean"}},
   "required":["name","phone","nature"]}}
```
Side effect: immediate SMS to whoever is on call. Also creates the lead.

```json
{"name": "answer_question",
 "inputSchema": {"type":"object",
   "properties":{"question":{"type":"string"}},"required":["question"]}}
```
Searches `knowledge_items`. Returns the answer or `{found: false}`, in which
case Mabel says someone will follow up rather than guessing.

Plus `book_estimate`, `get_job_history`, `log_note`.

**Every handler resolves tenant from the JWT, sets `SET LOCAL app.tenant_id`,
and filters on it. No handler accepts a tenant identifier as an argument.**

---

## Emergency rules

Per trade, versioned, in `vertical_rulesets`. Tenant overrides layer on top.

```json
{
  "trade": "plumbing",
  "version": 3,
  "triggers": [
    {"code":"ACTIVE_FLOODING","phrases":["water everywhere","flooding","pouring"],
     "severity":"wake_now"},
    {"code":"BURST_PIPE","phrases":["pipe burst","pipe broke"],"severity":"wake_now"},
    {"code":"SEWAGE_BACKUP","phrases":["sewage","backing up","raw sewage"],
     "severity":"wake_now"},
    {"code":"NO_WATER","phrases":["no water at all","whole house"],"severity":"wake_now"},
    {"code":"WATER_NEAR_ELECTRICAL","phrases":["near the panel","water and sparks"],
     "severity":"wake_now","safety_script":"advise_leave_and_call_911"},
    {"code":"WATER_HEATER_LEAK","phrases":["water heater leaking"],"severity":"morning"},
    {"code":"SLOW_DRAIN","phrases":["slow drain","clogged"],"severity":"routine"}
  ],
  "required_capture":["name","address","callback","problem","urgency","source"],
  "never_say":["price","estimate_range","hourly_rate","arrival_time"]
}
```

Phrases are hints for the prompt, not a matcher — the model classifies, the
ruleset tells it what the categories mean and what each one triggers.

Every ruleset change ships with fixtures: an input scenario and the expected
classification. CI runs them.

---

## Post-call

Runs on hangup, in the worker:

1. Pull recording and transcript from xAI, write to Supabase Storage
2. Write `calls`, `transcripts`, and a `communication_events` row
3. Resolve or create the contact — deterministic on phone, fuzzy flagged for
   review, never auto-merged on fuzzy alone
4. Finalize the lead
5. Compute cost in integer cents, update `usage_daily`
6. Enqueue notifications — emergency already fired mid-call; routine leads join
   the morning recap
7. Run QA checks: did she quote a price, miss an emergency, escalate a
   non-emergency at 2am, lose the caller under 20 seconds. Flag on `calls.qa_flags`.

---

## Owner SMS interface

Inbound owner texts hit a Telnyx webhook, parse to intents:

| Input | Action |
|---|---|
| `1` `2` `3` | Expand item N from the last list |
| `WON RUIZ 3800` | Mark won, set value cents |
| `LOST CHEN` | Mark lost |
| `HENDERSON` | That contact's thread summary |
| `FU` | Open follow-ups |
| `C` | Bridge a call to the last emergency caller |
| anything else | LLM intent + RAG over that tenant's thread |

Recall answers are composed against retrieved rows, capped at 160 GSM-7
characters, no emoji. State lives in `sms_sessions`, 24-hour TTL.

**Outbound messages the owner receives:** emergency alert (immediate), morning
recap (7am local), weekly summary (Monday), follow-up nudge, monthly report link.

---

## Constraints to design around

**10 concurrent sessions per team.** Monitor it, alert at 7, request a raise
from xAI well before you need it.

**120-minute max session.** Not a practical concern for this use case.

**Retention on xAI is short.** Archive post-call, always.

**The `model` param is ignored on SIP `call_id` sessions.** Pin it anyway for
direct sessions; don't assume it controls the SIP path.

**Fail safe:** if the media process can't answer, the call should fall through
to the carrier's voicemail. A Mabel outage means the contractor is back where
he started, not worse.
