-- Draft only. Sam runs this. A bot does not.
-- Per-shop xAI Voice Agent id on tenants. Nullable: onboard records it when
-- we have it. This PR does not call xAI and does not take an agent live.
-- Column rides existing tenants RLS. No DELETE. No money. This column is text.

BEGIN;

ALTER TABLE tenants
    ADD COLUMN xai_voice_agent_id text;

ALTER TABLE tenants
    ADD CONSTRAINT tenants_xai_voice_agent_id_not_blank
        CHECK (
            xai_voice_agent_id IS NULL
            OR length(trim(xai_voice_agent_id)) > 0
        );

COMMIT;
