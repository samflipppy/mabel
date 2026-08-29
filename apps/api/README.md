# Mabel API

FastAPI skeleton. She answers the phone. This service verifies the signed inbound-call webhook, resolves the tenant from the To number, and exposes MCP tools over Streamable HTTP.

It does not deploy. It does not run migrations. It does not take an agent live.
If Telnyx or xAI keys are missing, the webhook fails closed.

## Layout

```
src/mabel/
  voice/       # webhook, DID, pinned voice model, per-shop agent stub
  mcp/         # eight tools, tenant from the token
  shops/       # packet, onboard write path, POST /shops (admin, not MCP)
  leads/
  sms/
  reports/
  billing/
  platform/    # db tenant_scope, DID directory, no BYPASSRLS
tests/{unit,golden,property,integration}
```

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
- `MABEL_ADMIN_TOKEN` — required on `POST /shops`. Missing config is 503. Wrong token is 401. `onboard_shop()` does not read this.
- `XAI_WEBHOOK_SECRET` — verifies `webhook-id` / `webhook-timestamp` / `webhook-signature`
- `XAI_API_KEY` — if missing, the webhook fails closed and Mabel does not join the call
- `TELNYX_API_KEY` — if missing, the webhook fails closed and Mabel does not text the owner. On a matched emergency the lead is still saved; the SMS is recorded unsent with reason `telnyx not configured`. The key is never written to a file.
- `TELNYX_FROM_E164` — Mabel's From number for owner texts. Never the caller's callback. 10DLC: nothing goes to a real number until the campaign clears.

Voice model is pinned in code to `grok-voice-think-fast-2.0`. Not an env var. Never `grok-voice-latest`.

Each shop gets its own xAI Voice Agent from our template (she is Mabel, disclosure, never quote, never invent arrival, no clone, no `web_search`/`x_search`, eight tools). We do not click Customer Support. `xai_voice_agent_id` on the tenant is optional/null until we have it. This service does not call xAI to create the agent. Collections may hold non-price docs; dollar-looking uploads are rejected the same way as greeting notes.
