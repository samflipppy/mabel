# Running Mabel locally

Written after actually doing it, so the commands below are the ones that ran,
not the ones that ought to work. Where something does not work yet, it says so
rather than giving you a command that fails.

`SETUP.md` is the *production* credential list and is written against v1 — it
still points at `infra/0001_init.sql` and `python -m mabel.sms.recap_send`,
neither of which is how v2 works. Use this file for local work.

---

## 1. Dependencies

The `.venv` in the repo was built for the test suite, so it is missing
`uvicorn`, `stripe`, `websockets` and `pydantic-settings`. To get everything
the apps actually declare:

```bash
uv sync
```

Node, for the portal:

```bash
cd apps/web && npm install
```

## 2. A database

```bash
docker run -d --name mabel-db -e POSTGRES_PASSWORD=postgres -p 55432:5432 postgres:16
docker exec mabel-db psql -U postgres -c "CREATE DATABASE mabel_dev"
```

Then apply the schema. **This is a migration, so it is yours to run** — nothing
automated does it:

```bash
export MIGRATION_DATABASE_URL=postgresql://postgres:postgres@localhost:55432/mabel_dev
cd packages/db && uv run alembic upgrade head
```

The `cd` is not optional. `script_location` in `alembic.ini` is relative, so
running it from the repo root fails with "Path doesn't exist: mabel_db\migrations".
`uv run alembic heads` from that directory should print `0008_call_legs (head)`.

Revision `0001` creates the `mabel_app` and `mabel_admin` roles. `0002` needs
`pg_cron`, which stock `postgres:16` does not have — if it fails there, either
`upgrade 0001_v2_schema` and then `stamp` past `0002`, or use an image with
pg_cron. Nothing else depends on it locally; the crons only fill a queue you
can fill by hand.

## 3. Environment

Nothing invents a credential. Anything absent fails closed with a message
naming what is missing and the `docs/BLOCKED.md` entry that explains it.

```bash
export DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:55432/mabel_dev"
export MCP_TOKEN_SIGNING_KEY="anything-long-and-random-for-local-use"
export PORTAL_ORIGINS="http://localhost:3000"
export LOG_LEVEL=INFO
```

That is enough to boot the API. The rest — `XAI_API_KEY`, `TELNYX_API_KEY`,
`TELNYX_PUBLIC_KEY`, `TELNYX_MESSAGING_PROFILE_ID`, `STRIPE_SECRET_KEY`,
`STRIPE_WEBHOOK_SECRET`, `SUPABASE_JWT_SECRET` — gates the feature that needs
it and nothing else. See `docs/BLOCKED.md` for the full list of sixteen.

## 4. The three processes

```bash
# API + MCP server
uv run uvicorn mabel_api.main:app --reload --port 8000

# Background worker (queue, recaps, nudges, review requests)
uv run python -m mabel_worker.runner

# Portal
cd apps/web && npm run dev
```

The portal wants `apps/web/.env.local`:

```
NEXT_PUBLIC_API_BASE=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=...
NEXT_PUBLIC_SUPABASE_ANON_KEY=...
```

Without Supabase you can load the portal but not sign in, so most screens will
sit on their loading state. The API itself is fully exercisable with `curl`.

## 5. Check it came up

```bash
curl localhost:8000/health          # {"status":"ok","version":"2.0.0"}
curl localhost:8000/health/ready    # {"status":"ready"}
curl localhost:8000/mcp/health      # {"status":"ok","tools":9}
open  localhost:8000/docs           # all 44 routes
```

## 6. Drive the MCP server the way Grok does

This is the useful one. The voice model reaches Mabel over ordinary HTTPS
JSON-RPC, so you can be the voice model:

```bash
export MCP_TOKEN_SIGNING_KEY="anything-long-and-random-for-local-use"

TOKEN=$(uv run python -c "
from uuid import UUID
from mabel_mcp.tokens import mint_call_token
print(mint_call_token(UUID('<a real tenant id>'), 'local-demo-call'))
")

curl -s localhost:8000/mcp \
  -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Nine tools come back: `lookup_customer`, `get_service_area`,
`check_availability`, `create_lead`, `escalate_emergency`, `book_estimate`,
`get_job_history`, `answer_question`, `log_note`. Without the bearer token you
get `-32600 unauthorized`, which is the whole security model in one line — the
tenant is inside the signed token, not in the request body, so nothing the
model says can reach another shop's rows.

## 7. Run a whole call without a phone

No sockets, no model, no database. Thirty-odd recorded scenarios driven through
the real dispatcher, the real tool handlers, the real verticals engine and the
real post-call pass:

```bash
uv run pytest tests/simulation -q
```

This is the closest thing to "run a call locally" that exists today, and it is
the thing to add a scenario to when you change escalation behaviour.

## 8. The test suite

```bash
uv run pytest tests -q                      # 1400+, skips the DB-backed ones

export TEST_DATABASE_URL="postgresql://postgres:postgres@localhost:55432/mabel_test"
uv run pytest tests -q                      # all of them, ~3 minutes
```

`TEST_DATABASE_URL` must be a **scratch** database — the fixtures drop and
recreate `public`, and refuse outright if the URL looks production-shaped.

**One pytest session per database.** Two at once will pull the schema out from
under each other and produce a scatter of unique-violation errors in tests that
pass fine alone.

---

# How Mabel talks to Grok Voice

Two connections, in opposite directions. Getting this the wrong way round is
the usual confusion, so:

```
  homeowner
      │ dials the shop's number, which is call-forwarded
      ▼
  Telnyx DID ──SIP──▶ sip.voice.x.ai        (audio never touches us)
                          │
                          │ 1. POST realtime.call.incoming
                          ▼
                   ┌──────────────────┐
                   │  Mabel API       │
                   │  (FastAPI)       │
                   └──────────────────┘
                          │ 2. WSS wss://api.x.ai/v1/realtime?call_id=…
                          ▼
                       xAI Grok
                          │ 3. HTTPS JSON-RPC, bearer call token
                          ▼
                   ┌──────────────────┐
                   │  Mabel /mcp      │──▶ Postgres, inside tenant_scope
                   │  nine tools      │
                   └──────────────────┘
```

**The audio never reaches us.** Telnyx's SIP trunk points at `sip.voice.x.ai`
directly (FQDN, port 5060, A record). We are not in the media path, which is
why there is no RTP handling anywhere in this repo.

**1. The incoming-call webhook.** xAI POSTs `realtime.call.incoming`, signed
Standard-Webhooks style: HMAC-SHA256 over `{id}.{timestamp}.{raw_body}`,
verified in `packages/xai/mabel_xai/webhooks.py` against the raw bytes. The
handler resolves the tenant from the **dialed number**, server-side, via
`resolve_tenant_by_did`. Nothing a model ever says reaches a tenant lookup.

**2. The realtime socket.** We open
`wss://api.x.ai/v1/realtime?call_id=…` with the API key — ephemeral client
secrets are not supported for SIP `call_id` sessions. Then one
`session.update` (`config_builder.build_session_update()`) carrying:

- `instructions` from `prompt.render_prompt()` — greeting, services, hours,
  never-say list, knowledge base
- audio `audio/pcmu` at 8000Hz both directions, because it is a phone line
- `turn_detection`, and `keyterms` for street names an 8kHz line mangles
- the model pinned to `grok-voice-think-fast-2.0`. Never `grok-voice-latest`:
  a silent model change on a live phone line is not something to discover from
  a customer.
- **one** `tools` entry of `{"type": "mcp", "server_url": …}` pointing back at
  our `/mcp`, with a bearer token

Then the opening disclosure as a `force_message`, and *no* `response.create`
after it — sending one makes her say the disclosure and then talk over herself.

**3. Grok calls back into us.** Every tool call is an HTTPS JSON-RPC request to
`/mcp` carrying that bearer token: an HS256 JWT holding `tenant_id` and
`call_id`, minted at session open with a 15-minute TTL against a 120-minute
maximum session, so long calls need `CallToken.needs_refresh()` watched rather
than discovering the expiry when a tool fails mid-sentence at minute sixteen.

This is the part worth understanding: **the model has no database access and no
tenant identity of its own.** It has nine tools and a token. The token decides
whose rows it touches.

On hangup, `postcall.finalize()` writes the call, transcript, thread entry,
usage and — as of `feat/customer-sms` — the confirmation text to the caller, in
one transaction.

## What is not wired yet

Two honest gaps, and neither is a bug:

**`apps/media/src/mabel_media/session.py` is unimplemented, on purpose.** It is
Sam's file per 04-REPO.md — "the real-time path against a sparsely documented
API is where an agent writes confident, wrong code." What is there is the
interface, a `FakeSessionTransport` the tests and simulation harness bind to,
and TODOs. Everything it calls is finished and tested: the config builder, the
prompt renderer, `join_url()`, token minting, `finalize()`.

**There is no `realtime.call.incoming` route in the v2 API.** `/mcp` is mounted,
Telnyx and Stripe webhooks are mounted, the xAI one is not. The signature
verification exists in `packages/xai`; a v1 handler exists under
`apps/api/src/mabel/voice/`. Nothing in `mabel_api` currently gives xAI
somewhere to POST.

So: step 3 works end to end today and you can drive it yourself with the curl
above. Steps 1 and 2 do not exist yet, which means **no real call can be taken
locally or anywhere else** until that route is added and `session.py` is
written. Assumptions A1–A9 in `docs/xai_notes.md` are the things to check
against a real session when it happens.
