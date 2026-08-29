# Draft SQL

Sam runs these. A bot does not. Never against production.

- `0001_init.sql` — roles, tenants, DIDs, leads, notes, RLS
- `0002_shop_packet.sql` — shop packet columns on tenants plus `service_area_zips`
- `0003_xai_voice_agent.sql` — nullable `xai_voice_agent_id` on tenants (per-shop agent)
- `0004_archive_recap.sql` — call archives, recap queue, lead SMS flags, zip `retired_at`

Onboard writes tenant + inbound DID + zips under `SET LOCAL app.tenant_id`. `xai_voice_agent_id` is optional/null until we have the agent. This repo does not call xAI to create one. Zip replace retires rows; it does not DELETE.

