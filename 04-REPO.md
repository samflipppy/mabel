# Mabel v2 — Repo & Build Order

---

## Structure

```
mabel/
├── AGENTS.md
├── README.md
├── fly.toml
├── pyproject.toml                 # uv workspace
│
├── apps/
│   ├── api/                       # FastAPI — process group "web"
│   │   └── src/mabel_api/
│   │       ├── main.py
│   │       ├── deps.py            # auth, tenant context injection
│   │       ├── routes/
│   │       │   ├── calls.py
│   │       │   ├── leads.py
│   │       │   ├── contacts.py
│   │       │   ├── config.py      # agent config CRUD + publish
│   │       │   ├── knowledge.py
│   │       │   ├── reports.py
│   │       │   ├── usage.py
│   │       │   ├── integrations.py
│   │       │   ├── billing.py
│   │       │   └── onboarding.py
│   │       └── webhooks/
│   │           ├── xai.py         # realtime.call.incoming
│   │           ├── telnyx.py      # SMS inbound, call events
│   │           └── stripe.py
│   │
│   ├── media/                     # process group "media"
│   │   └── src/mabel_media/
│   │       ├── session.py         # ← SAM WRITES THIS. WebSocket relay.
│   │       ├── config_builder.py  # agent_config → session.update payload
│   │       ├── prompt.py          # prompt rendering
│   │       └── postcall.py        # archive, finalize, enqueue
│   │
│   ├── worker/                    # process group "worker"
│   │   └── src/mabel_worker/
│   │       ├── runner.py          # SKIP LOCKED loop
│   │       └── jobs/
│   │           ├── morning_recap.py
│   │           ├── weekly_summary.py
│   │           ├── followup_nudge.py
│   │           ├── silence_alert.py
│   │           ├── monthly_report.py
│   │           ├── purge_recording.py
│   │           └── qa_review.py
│   │
│   └── web/                       # Next.js 15 portal
│       ├── app/
│       │   ├── (marketing)/       # hiremabel.com
│       │   └── (app)/             # app.hiremabel.com
│       │       ├── dashboard/
│       │       ├── calls/
│       │       ├── leads/
│       │       ├── customers/
│       │       ├── mabel/         # config tabs
│       │       ├── reports/
│       │       ├── settings/
│       │       └── onboarding/
│       ├── components/
│       └── lib/api.ts             # generated from OpenAPI
│
├── packages/
│   ├── domain/                    # PURE. Pydantic models, no I/O.
│   ├── verticals/                 # PURE. Trade rulesets + engine.
│   │   ├── engine.py
│   │   ├── rulesets/
│   │   │   ├── plumbing.v3.json
│   │   │   ├── hvac.v2.json
│   │   │   ├── electrical.v2.json
│   │   │   ├── restoration.v1.json
│   │   │   ├── roofing.v1.json
│   │   │   ├── locksmith.v1.json
│   │   │   └── towing.v1.json
│   │   └── fixtures/
│   ├── db/
│   │   ├── tenant.py              # tenant_scope() — SET LOCAL helper
│   │   ├── queries/
│   │   └── migrations/            # Alembic
│   ├── mcp/
│   │   ├── server.py              # Streamable HTTP
│   │   └── tools/
│   ├── xai/
│   │   ├── client.py              # every xAI assumption lives here
│   │   └── webhooks.py            # signature verification
│   ├── telnyx/
│   ├── integrations/
│   │   ├── google_calendar.py
│   │   ├── jobber.py
│   │   └── housecall.py
│   └── sms/
│       ├── intents.py             # command grammar
│       ├── recall.py              # NL query over thread
│       └── compose.py             # GSM-7 message builders
│
├── tests/
│   ├── unit/
│   ├── golden/                    # ruleset fixtures
│   ├── property/                  # invariants
│   ├── isolation/                 # RLS cross-tenant tests
│   ├── integration/
│   └── simulation/                # ~30 recorded call scenarios
│
├── infra/                         # agents do not touch
└── docs/
    ├── xai_notes.md               # verified vs. assumed API behavior
    └── runbook.md
```

**Seams that matter:** `domain/` and `verticals/` are pure and I/O-free, so
they're trivially testable and safe for agents. `db/` owns every connection and
the tenant-context helper, so isolation lives in one place. `xai/client.py` is
the single file where API assumptions live. `media/session.py` is Sam's — the
real-time path against a sparsely documented API is where an agent writes
confident, wrong code.

---

## fly.toml

```toml
app = "mabel"
primary_region = "iad"

[build]
  dockerfile = "Dockerfile"

[deploy]
  release_command = "alembic upgrade head"
  strategy = "bluegreen"

[processes]
  web    = "uvicorn mabel_api.main:app --host 0.0.0.0 --port 8080"
  media  = "python -m mabel_media.session"
  worker = "python -m mabel_worker.runner"

[http_service]
  internal_port = 8080
  processes = ["web"]
  auto_stop_machines = false
  min_machines_running = 2

[[vm]]
  processes = ["web"]
  size = "shared-cpu-2x"

[[vm]]
  processes = ["media"]
  size = "shared-cpu-2x"

[[vm]]
  processes = ["worker"]
  size = "shared-cpu-1x"
```

`media` deploys drop in-flight calls — deploy it in the small hours.

---

## CI

Every PR:

```yaml
- ruff check + format --check
- pyright
- pytest tests/unit
- pytest tests/golden        # ruleset fixtures
- pytest tests/property      # invariants
- pytest tests/isolation     # RLS cross-tenant — never skip
- alembic upgrade head --sql # migration dry run
- npm run typecheck && npm run build
```

Nightly: `pytest tests/simulation` against staging.

---

## Build order

**Phase 1 — foundation**
Schema and migrations. `tenant_scope()`. RLS isolation tests. Domain models.
Verticals engine with plumbing, HVAC, electrical rulesets and fixtures.
*Done when: cross-tenant isolation tests pass and the rule engine is green.*

**Phase 2 — the call**
xAI client and webhook verification. Telnyx SIP setup. `media/session.py`
(Sam). Config builder and prompt rendering. MCP server with all nine tools.
Post-call archival.
*Done when: a real call to a real number creates a real lead.*

**Phase 3 — the owner**
Telnyx SMS. Emergency alerts. Morning recap. Worker and queue. Command grammar
and recall.
*Done when: Sam's own phone gets a useful 7am text.*

**Phase 4 — the portal**
Supabase Auth. Dashboard, Calls, Leads. Transcript search. Config screens with
the test-call button. Onboarding wizard.
*Done when: a contractor can change his own hours without emailing anyone.*

**Phase 5 — the money**
Stripe subscriptions. Usage tracking. Monthly reports with PDF. Billing screen.
*Done when: an invoice goes out automatically and the report explains it.*

**Phase 6 — integrations**
Google Calendar. Jobber. Outbound webhook. Housecall Pro.
*Done when: a lead lands in a customer's Jobber without anyone touching it.*

**Phase 7 — hardening**
Simulation harness. Observability spans. Retention jobs. Forwarding health.
Runbook.
