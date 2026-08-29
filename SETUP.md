# Hire Mabel

Plug these in. LLC and 10DLC live on the founder sheet already. This list is what lights her up once credentials exist. Fail closed without keys. A bot does not deploy and does not run SQL. No values belong in this file.

1. **Supabase / Postgres.** Sam runs the drafts in order: `infra/0001_init.sql`, `infra/0002_shop_packet.sql`, `infra/0003_xai_voice_agent.sql`, `infra/0004_archive_recap.sql`, `infra/0005_recap_send.sql`. App role is `mabel_app`. Never the migrator.

2. **Fly secrets (names only).** Set these on the API app. Never commit values.

   - `DATABASE_URL`
   - `MABEL_ADMIN_TOKEN`
   - `MABEL_MCP_TOKEN_SECRET`
   - `MABEL_MCP_PUBLIC_URL`
   - `XAI_API_KEY`
   - `XAI_WEBHOOK_SECRET`
   - `TELNYX_API_KEY`
   - `TELNYX_FROM_E164`

3. **Telnyx SIP.** Voice Suite → SIP Trunking → FQDN connection. FQDN is `sip.voice.x.ai`. Port 5060, record type A.

4. **xAI webhook.** Point it at `https://<fly-app>/voice/webhook`.

5. **`TELNYX_FROM_E164`.** Mabel's From number for owner texts. Never the caller's callback. Nothing goes to a real number until 10DLC clears.

6. **`MABEL_MCP_PUBLIC_URL`.** `https://<fly-app>/mcp`

7. **Voice agent.** Onboard creates the per-shop agent from our template when `XAI_API_KEY` is set (`POST https://api.x.ai/v1/voice-agents`). That create route is not in the public docs.x.ai reference yet. If the call fails closed, log into console.x.ai and mint the agent from our template (Mabel, disclosure, never quote, never invent arrival, pin `grok-voice-think-fast-2.0`, no clone, no `web_search` / `x_search`, eight MCP tools). Store the id on the tenant. Never invent a key.

8. **7am recap.** Not a cron in this repo. After Telnyx works: `python -m mabel.sms.recap_send`

Health check is `GET /health`.

Hire Mabel.
