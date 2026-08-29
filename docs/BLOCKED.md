# Blocked on accounts Sam hasn't created

Mabel fails closed without credentials. Nothing here is stubbed with a fake
key — the code assumes the account exists, reads the secret from the
environment, and refuses to run when it isn't there.

Each entry: what's needed, why, and what stays dark until it lands.

| # | Service | Secret / resource | Why | Blocked until then |
|---|---|---|---|---|
| 1 | Supabase | `DATABASE_URL` (project Postgres, `mabel_app` role), `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` | Every table, RLS, Auth, and Storage | Migrations can't be applied. RLS isolation tests need a live Postgres; they skip with a loud reason when `TEST_DATABASE_URL` is unset. Portal login. |
| 2 | Supabase Storage | Private buckets `recordings`, `transcripts`, `reports` | Post-call archival (invariant 7), monthly report PDFs | `postcall` archive step raises `ArchiveUnavailable` rather than dropping a recording silently. |
| 3 | Telnyx | `TELNYX_API_KEY`, `TELNYX_PUBLIC_KEY` (Ed25519, for webhook verify), messaging profile id, SIP FQDN connection | Inbound SIP into `sip.voice.x.ai`, DIDs, all SMS | No DIDs to assign, so no tenant can be resolved from a dialed number. SMS send is a no-op that records `notifications.status='failed'` with `error='TELNYX_API_KEY unset'`. |
| 4 | Telnyx A2P | 10DLC brand + campaign registration | Owner SMS is application-to-person; unregistered traffic gets filtered | Emergency alerts and morning recaps may be silently dropped by carriers even once the key exists. This is a lead-time item — register early. |
| 5 | xAI | `XAI_API_KEY`, per-number `XAI_WEBHOOK_SECRET` (`dispatch_signing_secret`) | Voice agent, the realtime WebSocket, webhook signature verification | `packages/xai/client.py` refuses to construct without a key and refuses entirely under pytest. No call can be joined. |
| 6 | xAI | Concurrency raise above the default **10 concurrent sessions per team** | 10 sessions caps us at roughly 10 simultaneous customers mid-call | Not blocking today. Alert fires at 7. Request the raise well before it bites. |
| 7 | xAI | Confirmation of the Voice Agents create route | `docs/xai_notes.md` records `POST https://api.x.ai/v1/voice-agents` as an **assumption** — it is not in public docs as of 2026-08-29 | Onboarding leaves `tenants.xai_agent_id` NULL and the shop still drafts. Agent can be minted by hand in console.x.ai meanwhile. |
| 8 | Stripe | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, price ids for the `mabel` / `fulltime` / `plus` plans | Subscriptions, overage, customer portal | Phase 5 billing routes return 503. Usage is still tracked in `usage_daily` — that's ours and doesn't need Stripe. |
| 9 | Resend | `RESEND_API_KEY`, verified sending domain for `hiremabel.com` | Recap and report email, magic links | Email notifications record `failed`. SMS path is unaffected. |
| 10 | Google Cloud | OAuth client id/secret, Calendar API enabled, verified consent screen | `check_availability` reading real slots, writing estimate appointments | `check_availability` falls back to the tenant's configured default windows, which is the documented behaviour anyway. Mabel still never invents a time. |
| 11 | Jobber | Developer app, OAuth client id/secret, `write_requests` scope | Push leads as Jobber Requests | Integration shows `status='error'` with a clear message; leads still land in Mabel. |
| 12 | Housecall Pro | API key, and the customer must be on the **MAX** plan | Push leads to Job Inbox | Phase 6, v2.1. Gated behind a plan check that we cannot test without an account. |
| 13 | Sentry / Axiom / Better Stack | `SENTRY_DSN`, `AXIOM_TOKEN` + dataset, Better Stack heartbeat URL | Errors, structured logs with `call_id` correlation, the 3am pager | Spans are emitted to stdout as structured JSON. Nothing is lost, it just isn't queryable. |
| 14 | Supabase vault | Vault configured, so `integrations.vault_key` resolves | Integration OAuth tokens are referenced by key and never stored in our tables | Integration connect flows can't complete. We will not fall back to storing a token in `integrations.config`. |

## Rules this file exists to enforce

- **Never invent or stub a credential.** Not in a file, not in `.env.example`,
  not in a test. A fake key that "works locally" is how a real one ends up
  committed.
- **Fail closed.** Missing secret means refuse, log, and surface the reason —
  never degrade into a mock that looks like it worked.
- **Never hold a tenant's credentials.** Their Jobber token lives in the vault
  under `integrations.vault_key`. We hold the key name, not the secret.
