# Draft SQL

Sam runs these. A bot does not. Never against production.

- `0001_init.sql` — roles, tenants, DIDs, leads, notes, RLS
- `0002_shop_packet.sql` — shop packet columns on tenants plus `service_area_zips`

Onboard writes tenant + inbound DID + zips under `SET LOCAL app.tenant_id`. Those two files have the columns. Do not add a third unless one is missing.

