"""Lead and note records. dollars_won is owner-entered later. Never filled by a model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Lead:
    id: UUID
    tenant_id: UUID
    name: str
    address: str
    callback: str
    problem: str
    urgency: str
    source: str
    emergency_code: str | None = None
    # Owner-entered later. Never filled by a model.
    dollars_won: Decimal | None = None
    created_at: datetime = field(default_factory=_utcnow)
    sms_sent: bool | None = None
    sms_reason: str | None = None


@dataclass
class Note:
    id: UUID
    tenant_id: UUID
    body: str
