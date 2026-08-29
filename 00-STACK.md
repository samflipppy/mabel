# Mabel v2 — Stack

Full product. Voice agent, client portal, billing, integrations.

---

## Runtime & services

| Layer | Choice | Why |
|---|---|---|
| Voice agent | xAI Grok Voice Agent API | Speech-to-speech, MCP-native, $0.08/min |
| SIP / telephony | Telnyx | ~half Twilio's per-minute, owned network, clean SIP into `sip.voice.x.ai` |
| SMS | Telnyx | Same account as SIP, one A2P registration path |
| Call-path service | Fly.io — Python 3.12 | Always-on, long-lived WebSockets. Serverless can't hold a call. |
| API + workers | Fly.io — same app, separate processes | `web`, `media`, `worker` process groups |
| Database | Supabase Postgres 16 | RLS-native, PITR, connection pooling |
| Object storage | Supabase Storage | Recordings, photos. Private buckets, signed URLs |
| Scheduled jobs | Supabase `pg_cron` + Postgres queue | Recaps, sweeps, retention, reports |
| Portal | Next.js 15 (App Router) on Fly | Same provider as the API. TypeScript, Tailwind, shadcn/ui |
| Portal auth | Supabase Auth | Magic link + password. Maps to `users.tenant_id` |
| Billing | Stripe | Subscriptions, usage overage, customer portal |
| Email | Resend | Recaps, reports, magic links |
| Errors | Sentry | Python + JS |
| Logs & traces | Axiom | Structured JSON, `call_id` correlation |
| Uptime & paging | Better Stack | The 3am pager |
| CI/CD | GitHub Actions → Fly | Migrations via `release_command` |
| Secrets | Fly secrets + Supabase vault | Never in repo |

---

## Backend detail

**Python 3.12**, `uv` for dependencies, **FastAPI** for HTTP, **Pydantic v2** for
all boundaries, **SQLAlchemy 2.0 Core** for queries (not the ORM — tenant-scoped
raw-ish SQL is clearer and RLS-safer), **Alembic** for migrations.

**Process groups in `fly.toml`:**
- `web` — FastAPI: portal API, webhooks, MCP server
- `media` — the xAI WebSocket relay. Isolated so a portal deploy never drops a live call.
- `worker` — Postgres `SKIP LOCKED` queue runner

**Why `media` is its own process:** a slow request on the portal API shouldn't
stall the event loop pumping audio to a homeowner. Same codebase, same deploy,
different process — you get isolation without a second service to operate.

---

## Frontend detail

Next.js 15 App Router, TypeScript strict, Tailwind, shadcn/ui, TanStack Query
for server state, Recharts for the usage graphs. Types generated from the
FastAPI OpenAPI schema so the contract can't drift.

Two surfaces:
- **`hiremabel.com`** — marketing. Static, server-rendered.
- **`app.hiremabel.com`** — the client portal.

---

## Voice layer

- Model pinned: `grok-voice-think-fast-2.0`
- Transport: SIP from Telnyx → `sip:{number}@sip.voice.x.ai;transport=tls`
- Session: inbound `realtime.call.incoming` webhook → resolve tenant from dialed
  number → open `wss://api.x.ai/v1/realtime?call_id={call_id}` → `session.update`
- Tools: our own MCP server over Streamable HTTP (xAI does not support stdio)
- Turn detection: `server_vad`
- Audio: G.711 μ-law 8kHz, no transcoding

**Known xAI constraints to design around:**
- 10 concurrent sessions per team — monitor and request a raise early
- 120-minute max session
- The `model` param is ignored on SIP `call_id` sessions; the session binds to
  the inbound call. Pin it anyway for direct sessions.
- Conversation retention is short. Archive everything post-call.

---

## Third-party integrations (portal-configurable)

| Integration | Direction | Ships |
|---|---|---|
| Google Calendar | Read availability, write estimate appointments | v2 |
| Jobber | Push leads as Requests | v2 |
| Housecall Pro | Push leads to Job Inbox (MAX plan only) | v2.1 |
| Zapier / webhooks | Outbound lead events | v2.1 |
| QuickBooks | Deferred | — |

---

## Environments

`local` (Docker Compose + Supabase local), `staging` (own Fly app + Supabase
project), `production`. Agents work only against `local` and `staging`.

---

## Cost model

Per customer per month at ~90 voice minutes:

| | |
|---|---|
| xAI voice | $7.20 |
| Telnyx (DID + minutes + SMS) | ~$2.50 |
| MCP tool calls | ~$0.30 |
| Storage | ~$0.30 |
| **Variable** | **~$10** |

Fixed: Fly ~$60, Supabase Pro $25, Axiom $25, Better Stack $29, Sentry $0,
Resend $20 ≈ **$160/mo**.

| Customers | Fixed/customer | All-in | Margin @ $299 |
|---|---|---|---|
| 5 | $32 | $42 | 86% |
| 25 | $6.40 | $16 | 95% |
| 100 | $1.60 | $12 | 96% |
| 250 | $0.64 | $11 | 96% |
