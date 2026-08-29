"""The live agent config, the knowledge base, and the service area.

Everything the call path reads to decide how Mabel behaves on this call for
this tenant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True, slots=True)
class LiveConfig:
    id: UUID
    version: int
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
    emergency_overrides: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class KnowledgeRow:
    question: str
    answer: str


async def live_config(conn: AsyncConnection) -> LiveConfig | None:
    """The one published config for this tenant.

    The unique partial index `ix_agent_config_live` guarantees at most one row
    with `is_live`, so this cannot silently pick between two.
    """
    result = await conn.execute(
        text(
            """
            SELECT id, version, greeting, voice, speaking_rate, services,
                   service_area_zips, service_area_note, business_hours,
                   after_hours_only, never_say, custom_rules, keyterms,
                   emergency_overrides
            FROM agent_configs
            WHERE is_live
            """
        )
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    return LiveConfig(
        id=row["id"],
        version=row["version"],
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
    )


async def knowledge(conn: AsyncConnection) -> list[KnowledgeRow]:
    """The Q&A pairs, in the order the owner sorted them."""
    result = await conn.execute(
        text(
            """
            SELECT question, answer
            FROM knowledge_items
            WHERE is_active
            ORDER BY sort_order, updated_at
            """
        )
    )
    return [KnowledgeRow(question=r["question"], answer=r["answer"]) for r in result.mappings()]


async def search_knowledge(
    conn: AsyncConnection, question: str, *, limit: int = 1
) -> list[KnowledgeRow]:
    """What `answer_question` calls.

    Trigram similarity against the stored question, because a caller phrases it
    their own way. Returns nothing rather than the least-bad row when nothing
    is close — the tool's whole purpose is to let her say 'someone will follow
    up' instead of guessing.
    """
    result = await conn.execute(
        text(
            """
            SELECT question, answer, similarity(question, :q) AS score
            FROM knowledge_items
            WHERE is_active AND similarity(question, :q) >= 0.3
            ORDER BY score DESC
            LIMIT :limit
            """
        ),
        {"q": question, "limit": limit},
    )
    return [KnowledgeRow(question=r["question"], answer=r["answer"]) for r in result.mappings()]


async def tenant_by_did(conn: AsyncConnection, did_e164: str) -> dict[str, Any] | None:
    """Resolve the dialed number to a tenant. Invariant 3.

    Runs through `admin_scope()`, because at this moment there is no tenant
    context — which tenant it is *is* the question. `tenants` has RLS forced on
    it, so a plain SELECT here returns zero rows however correct it looks.

    The lookup goes through `resolve_tenant_by_did`, a SECURITY DEFINER
    function added in migration 0003. See that file for why a function rather
    than a BYPASSRLS connection: the blast radius is one lookup returning
    routing facts, not a process-wide ability to read every tenant.

    Nothing the model says reaches this. The number came from the SIP To
    header, before the socket opened.
    """
    result = await conn.execute(
        text(
            "SELECT tenant_id, location_id, business_name, trade, timezone, status, "
            "xai_agent_id FROM resolve_tenant_by_did(:did)"
        ),
        {"did": did_e164},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None
