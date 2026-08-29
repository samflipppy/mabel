# Mabel API

FastAPI. She answers the phone. This service verifies the signed inbound-call webhook, resolves the tenant from the To number, joins the realtime session when keys are present, and exposes MCP tools over Streamable HTTP.

It does not deploy. It does not run migrations. It does not take an agent live.
If Telnyx or xAI keys are missing, the webhook fails closed.

## Layout

```
src/mabel/
  voice/       # webhook, DID, pinned voice model, session join, archive, per-shop agent
  mcp/         # eight tools, tenant from the token
  shops/       # packet, onboard, settings PATCH, POST /shops (admin, not MCP)
  leads/
  sms/
  reports/
  billing/
  platform/    # db tenant_scope, DID directory, no BYPASSRLS
tests/{unit,golden,property,integration}
```

## Run locally

Names only. Put values in your shell, never in a file in this repo.

```bash
cd apps/api
python -m pip install -e "../../packages/verticals"
python -m pip install -e ".[dev]"
export MABEL_ADMIN_TOKEN
export MABEL_MCP_TOKEN_SECRET
export XAI_WEBHOOK_SECRET
# Optional. Without these, the webhook returns 503 and owner SMS stays unsent.
export XAI_API_KEY
export TELNYX_API_KEY
export TELNYX_FROM_E164
export DATABASE_URL
export MABEL_MCP_PUBLIC_URL
python -m uvicorn mabel.app:app --reload --app-dir src
```

Office dashboard (separate terminal):

```bash
cd apps/web
npm install
npm run dev
```

Then:

1. `POST /shops` with `Authorization: Bearer` and `MABEL_ADMIN_TOKEN`. Status stays `draft`. `live` stays false.
2. `PATCH /shops/{tenant_id}` for shop name, hours, timezone, owner SMS, service-area zips, greeting notes. Dollar-looking notes are 400. Emergency rules are not on this route.
3. Signed `POST /voice/webhook` joins through `FakeSessionTransport` in tests. Production opens a WebSocket only when `XAI_API_KEY` and `XAI_WEBHOOK_SECRET` are set, and never under pytest.
4. `escalate_emergency` texts the owner when a vertical rule matches, or records the SMS unsent if Telnyx is missing. Non-emergencies queue a 7am recap. Send due recaps with `python -m mabel.sms.recap_send`. Not a cron.
5. Overnight recap: `GET /shops/{tenant_id}/overnight`. Empty if there were no leads.

Voice model is pinned in code to `grok-voice-think-fast-2.0`. Not an env var.

## Run tests

```bash
python -m pip install -e "../../packages/verticals"
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check src tests
```

## Settings

Names only. Never put Telnyx, xAI, Jobber, or Stripe keys in a file, an env example, or a chat.

- `DATABASE_URL` — app role, not superuser, not the migrator
- `MABEL_MCP_TOKEN_SECRET` — signs short-lived tenant tokens we mint after DID resolution
- `MABEL_ADMIN_TOKEN` — required on `POST /shops`, `GET`/`PATCH /shops/{tenant_id}`, and overnight. Missing config is 503. Wrong token is 401. `onboard_shop()` does not read this.
- `MABEL_MCP_PUBLIC_URL` — MCP URL placed on `session.update`. Local default is `http://127.0.0.1:8000/mcp`
- `XAI_WEBHOOK_SECRET` — verifies `webhook-id` / `webhook-timestamp` / `webhook-signature`
- `XAI_API_KEY` — if missing, the webhook fails closed and Mabel does not join the call
- `TELNYX_API_KEY` — if missing, the webhook fails closed and Mabel does not text the owner. On a matched emergency the lead is still saved; the SMS is recorded unsent with reason `telnyx not configured`. The key is never written to a file.
- `TELNYX_FROM_E164` — Mabel's From number for owner texts. Never the caller's callback. 10DLC: nothing goes to a real number until the campaign clears.

Voice model is pinned in code to `grok-voice-think-fast-2.0`. Not an env var. Never `grok-voice-latest`.

Each shop gets its own xAI Voice Agent from our template (she is Mabel, disclosure, never quote, never invent arrival, no clone, no `web_search`/`x_search`, eight tools at `MABEL_MCP_PUBLIC_URL`). We do not click Customer Support. Onboard creates the agent when `XAI_API_KEY` is set and stores `xai_voice_agent_id`. Without the key, or if create fails, the id stays null and the shop still drafts. Tests use `FakeXaiAgentsClient`. The production client refuses under pytest. The create route is not in the public docs.x.ai reference; console.x.ai can still mint the agent until that API is confirmed. Never invent a key. Collections may hold non-price docs; dollar-looking uploads are rejected the same way as greeting notes.
