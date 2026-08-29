# Draft SQL

Sam runs these. A bot does not. Never against production.

- `0001_init.sql` — roles, tenants, DIDs, leads, notes, RLS
- `0002_shop_packet.sql` — shop packet columns on tenants plus `service_area_zips`
- `0003_xai_voice_agent.sql` — nullable `xai_voice_agent_id` on tenants (per-shop agent)
- `0004_archive_recap.sql` — call archives, recap queue, lead SMS flags, zip `retired_at`
- `0005_recap_send.sql` — `app.due_recap_tenants` (read-only SECURITY DEFINER, same pattern as DID resolve)

Onboard writes tenant + inbound DID + zips under `SET LOCAL app.tenant_id`. When `XAI_API_KEY` is set, onboard creates the per-shop agent and stores `xai_voice_agent_id`. Without the key the column stays null. Zip replace retires rows; it does not DELETE. Recap send sets `sent_at`; it does not DELETE.

