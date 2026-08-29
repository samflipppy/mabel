"""The Mabel configuration screen. 02-PORTAL.md calls it the differentiator:
"Everyone else makes you email support to change your hours."

Configs are versioned, and editing never touches the live one. A draft is a new
row; publishing flips `is_live`, which the unique partial index guarantees can
only ever be one row. That makes Revert a matter of publishing an older
version rather than a matter of remembering what the old values were.

**Publishing runs the same money check the prompt renderer does.** A greeting
or a custom rule with a price in it is rejected at publish time, in front of a
human who can fix it, rather than at 2am in front of a homeowner.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from mabel_media.prompt import PromptError, assert_no_money
from mabel_verticals.loader import TRADES, load_latest
from pydantic import BaseModel, Field
from sqlalchemy import text

from mabel_api.deps import CurrentUser, CurrentUserDep, TenantConn, require_role

router = APIRouter(prefix="/api/config", tags=["config"])


class DayHours(BaseModel):
    open: str
    close: str


class ConfigDraft(BaseModel):
    """What the portal sends. Every tab writes into the same row."""

    greeting: str = Field(min_length=1, max_length=500)
    voice: str = "carina"
    speaking_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    services: list[str] = Field(default_factory=list)
    services_declined: list[str] = Field(default_factory=list)
    service_area_zips: list[str] = Field(default_factory=list)
    service_area_note: str | None = None
    business_hours: dict[str, DayHours | None] = Field(default_factory=dict)
    after_hours_only: bool = True
    keyterms: list[str] = Field(default_factory=list)
    custom_rules: str | None = None
    emergency_overrides: dict[str, Any] = Field(default_factory=dict)


class ConfigVersion(BaseModel):
    id: str
    version: int
    is_live: bool
    greeting: str
    voice: str
    speaking_rate: float
    services: list[str]
    service_area_zips: list[str]
    service_area_note: str | None
    business_hours: dict[str, Any]
    after_hours_only: bool
    never_say: list[str]
    custom_rules: str | None
    keyterms: list[str]
    emergency_overrides: dict[str, Any]
    created_at: datetime
    published_at: datetime | None
    created_by_email: str | None


class TriggerToggle(BaseModel):
    """One row on the Emergencies tab. Plain English, not a JSON key."""

    code: str
    label: str
    severity: str
    default_severity: str
    enabled: bool
    has_safety_script: bool


@router.get("/current", response_model=ConfigVersion)
async def get_current(user: CurrentUserDep, conn: TenantConn) -> ConfigVersion:
    del user
    result = await conn.execute(
        text(
            """
            SELECT c.*, u.email AS created_by_email
            FROM agent_configs c
            LEFT JOIN users u ON u.id = c.created_by
            WHERE c.is_live
            """
        )
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No published configuration yet. Finish onboarding first.",
        )
    return _to_version(row)


@router.get("/versions", response_model=list[ConfigVersion])
async def list_versions(user: CurrentUserDep, conn: TenantConn) -> list[ConfigVersion]:
    """The change log. 02-PORTAL.md: "Every config edit, who made it, when,
    with a Revert button. Config is versioned in the schema — use it." """
    del user
    result = await conn.execute(
        text(
            """
            SELECT c.*, u.email AS created_by_email
            FROM agent_configs c
            LEFT JOIN users u ON u.id = c.created_by
            ORDER BY c.version DESC
            LIMIT 50
            """
        )
    )
    return [_to_version(row) for row in result.mappings()]


@router.post("/draft", response_model=ConfigVersion)
async def save_draft(
    body: ConfigDraft,
    user: CurrentUserDep,
    conn: TenantConn,
    _guard: CurrentUser = Depends(require_role("owner", "office")),
) -> ConfigVersion:
    """Save a new version. Never touches the live one.

    A draft is a new row rather than an update, so the config Mabel is running
    right now cannot change under a call in progress.
    """
    import json

    _reject_money(body)

    result = await conn.execute(
        text(
            """
            INSERT INTO agent_configs
              (tenant_id, version, is_live, greeting, voice, speaking_rate,
               services, service_area_zips, service_area_note, business_hours,
               after_hours_only, custom_rules, keyterms, emergency_overrides,
               created_by)
            VALUES
              (:tenant_id,
               (SELECT coalesce(max(version), 0) + 1 FROM agent_configs),
               false, :greeting, :voice, :rate, :services, :zips, :note,
               cast(:hours as jsonb), :after_hours_only, :custom_rules,
               :keyterms, cast(:overrides as jsonb), :created_by)
            RETURNING id
            """
        ),
        {
            "tenant_id": user.tenant_id,
            "greeting": body.greeting,
            "voice": body.voice,
            "rate": body.speaking_rate,
            "services": body.services,
            "zips": body.service_area_zips,
            "note": body.service_area_note,
            "hours": json.dumps(
                {
                    day: ({"open": h.open, "close": h.close} if h else None)
                    for day, h in body.business_hours.items()
                }
            ),
            "after_hours_only": body.after_hours_only,
            "custom_rules": body.custom_rules,
            "keyterms": body.keyterms,
            "overrides": json.dumps(body.emergency_overrides),
            "created_by": user.user_id,
        },
    )
    return await _by_id(conn, result.scalar_one())


@router.post("/{config_id}/publish", response_model=ConfigVersion)
async def publish(
    config_id: str,
    user: CurrentUserDep,
    conn: TenantConn,
    _guard: CurrentUser = Depends(require_role("owner", "office")),
) -> ConfigVersion:
    """Make a version live. Also the Revert button — reverting is publishing an
    older version, so nothing has to remember what the old values were.

    The unique partial index on `is_live` means the unset and the set have to
    happen in one transaction, which they do: `tenant_conn` holds it open.
    """
    draft = await _by_id(conn, UUID(config_id))
    _reject_money_on_version(draft)

    await conn.execute(text("UPDATE agent_configs SET is_live = false WHERE is_live"))
    result = await conn.execute(
        text(
            "UPDATE agent_configs SET is_live = true, published_at = now() "
            "WHERE id = :id RETURNING id"
        ),
        {"id": config_id},
    )
    if result.first() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such version")

    await _audit(conn, user, config_id, "config_published")
    return await _by_id(conn, UUID(config_id))


@router.get("/emergencies", response_model=list[TriggerToggle])
async def get_emergency_toggles(user: CurrentUserDep, conn: TenantConn) -> list[TriggerToggle]:
    """The Emergencies tab: the trade ruleset as plain-English toggles.

    "Burst pipe or active flooding → wake me". The label comes from the
    ruleset, which is why every trigger is required to have one.
    """
    del user
    trade_row = await conn.execute(text("SELECT trade FROM tenants LIMIT 1"))
    trade = trade_row.scalar_one_or_none() or ""

    if trade not in TRADES:
        return []

    config = await conn.execute(text("SELECT emergency_overrides FROM agent_configs WHERE is_live"))
    overrides = dict(config.scalar_one_or_none() or {})

    ruleset = load_latest(trade)
    toggles: list[TriggerToggle] = []
    for trigger in ruleset.triggers:
        override = overrides.get(trigger.code) or {}
        toggles.append(
            TriggerToggle(
                code=trigger.code,
                label=trigger.label,
                severity=str(override.get("severity") or trigger.severity),
                default_severity=str(trigger.severity),
                enabled=override.get("enabled") is not False,
                has_safety_script=trigger.safety_script is not None,
            )
        )
    return toggles


class KnowledgeItem(BaseModel):
    id: str | None = None
    question: str = Field(min_length=1, max_length=300)
    answer: str = Field(min_length=1, max_length=600)
    sort_order: int = 0
    is_active: bool = True


@router.get("/knowledge", response_model=list[KnowledgeItem])
async def list_knowledge(user: CurrentUserDep, conn: TenantConn) -> list[KnowledgeItem]:
    del user
    result = await conn.execute(
        text(
            "SELECT id, question, answer, sort_order, is_active FROM knowledge_items "
            "ORDER BY sort_order, updated_at"
        )
    )
    return [
        KnowledgeItem(
            id=str(row["id"]),
            question=row["question"],
            answer=row["answer"],
            sort_order=row["sort_order"],
            is_active=row["is_active"],
        )
        for row in result.mappings()
    ]


@router.put("/knowledge", response_model=list[KnowledgeItem])
async def replace_knowledge(
    items: list[KnowledgeItem],
    user: CurrentUserDep,
    conn: TenantConn,
    _guard: CurrentUser = Depends(require_role("owner", "office")),
) -> list[KnowledgeItem]:
    """Replace the whole Q&A list.

    Whole-list rather than per-item because the tab is sortable and toggleable
    and the browser already holds the intended final state. Reconciling
    individual edits would mean an ordering protocol for no benefit.
    """
    for item in items:
        # She reads these out verbatim. A price in one is a price she quotes.
        try:
            assert_no_money(f"{item.question} {item.answer}")
        except PromptError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"“{item.question[:40]}…” contains an amount. Mabel reads these "
                    f"answers out word for word, so she'd be quoting a price. {exc}"
                ),
            ) from exc

    await conn.execute(text("DELETE FROM knowledge_items"))
    for index, item in enumerate(items):
        await conn.execute(
            text(
                "INSERT INTO knowledge_items "
                "(tenant_id, question, answer, sort_order, is_active) "
                "VALUES (:t, :q, :a, :o, :active)"
            ),
            {
                "t": user.tenant_id,
                "q": item.question,
                "a": item.answer,
                "o": index,
                "active": item.is_active,
            },
        )
    return await list_knowledge(user, conn)


def _reject_money(body: ConfigDraft) -> None:
    """The same check the prompt renderer runs, at the point a human can fix it.

    A greeting reading "service calls from $89" is a price Mabel will say. The
    error names the field so the portal can put the message next to it.
    """
    for field, value in (
        ("greeting", body.greeting),
        ("custom rules", body.custom_rules or ""),
        ("out-of-area message", body.service_area_note or ""),
    ):
        try:
            assert_no_money(value)
        except PromptError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Your {field} has an amount in it. Mabel can talk about a job "
                    f"but she must never quote one. {exc}"
                ),
            ) from exc


def _reject_money_on_version(version: ConfigVersion) -> None:
    for value in (version.greeting, version.custom_rules or "", version.service_area_note or ""):
        try:
            assert_no_money(value)
        except PromptError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"That version contains an amount and can't go live. {exc}",
            ) from exc


async def _by_id(conn: Any, config_id: UUID) -> ConfigVersion:
    result = await conn.execute(
        text(
            """
            SELECT c.*, u.email AS created_by_email
            FROM agent_configs c
            LEFT JOIN users u ON u.id = c.created_by
            WHERE c.id = :id
            """
        ),
        {"id": config_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such version")
    return _to_version(row)


def _to_version(row: Any) -> ConfigVersion:
    return ConfigVersion(
        id=str(row["id"]),
        version=row["version"],
        is_live=row["is_live"],
        greeting=row["greeting"],
        voice=row["voice"],
        speaking_rate=float(row["speaking_rate"]),
        services=list(row["services"] or []),
        service_area_zips=list(row["service_area_zips"] or []),
        service_area_note=row["service_area_note"],
        business_hours=dict(row["business_hours"] or {}),
        after_hours_only=row["after_hours_only"],
        never_say=list(row["never_say"] or []),
        custom_rules=row["custom_rules"],
        keyterms=list(row["keyterms"] or []),
        emergency_overrides=dict(row["emergency_overrides"] or {}),
        created_at=row["created_at"],
        published_at=row["published_at"],
        created_by_email=row["created_by_email"],
    )


async def _audit(conn: Any, user: CurrentUser, entity_id: str, action: str) -> None:
    await conn.execute(
        text(
            "INSERT INTO audit_log (tenant_id, actor_id, actor_type, action, entity, entity_id) "
            "VALUES (:t, :actor, 'user', :action, 'agent_config', :entity)"
        ),
        {"t": user.tenant_id, "actor": user.user_id, "action": action, "entity": entity_id},
    )
