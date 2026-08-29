# Mabel API

FastAPI skeleton. She answers the phone. This service verifies the signed inbound-call webhook, resolves the tenant from the To number, and exposes MCP tools over Streamable HTTP.

It does not deploy. It does not run migrations. It does not call xAI until the keys are present, and even then joining a live call is a later change that needs Sam's sign-off.

## Layout

```
src/mabel/
  voice/       # webhook, DID, pinned voice model
  mcp/         # eight tools, tenant from the token
  leads/
  sms/
  reports/
  billing/
  platform/    # db tenant_scope, no BYPASSRLS
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
- `XAI_WEBHOOK_SECRET` — verifies `webhook-id` / `webhook-timestamp` / `webhook-signature`
- `XAI_API_KEY` — if missing, the webhook fails closed and Mabel does not join the call

Voice model is pinned in code to `grok-voice-think-fast-2.0`. Not an env var. Never `grok-voice-latest`.
