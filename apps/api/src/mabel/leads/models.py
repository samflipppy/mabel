"""Lead and note records. dollars_won is owner-entered later. Never filled by a model."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID


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


@dataclass
class Note:
    id: UUID
    tenant_id: UUID
    body: str
