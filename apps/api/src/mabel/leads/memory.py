"""In-memory leads and notes. Used when DATABASE_URL is unset (unit tests)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from mabel.leads.models import Lead, Note


@dataclass
class Store:
    leads: list[Lead] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    customers: list[dict[str, Any]] = field(default_factory=list)
    jobs: list[dict[str, Any]] = field(default_factory=list)

    def for_tenant(self, tenant_id: UUID) -> list[Lead]:
        return [lead for lead in self.leads if lead.tenant_id == tenant_id]

    def notes_for_tenant(self, tenant_id: UUID) -> list[Note]:
        return [note for note in self.notes if note.tenant_id == tenant_id]


_store = Store()


def store() -> Store:
    return _store


def reset_memory_store() -> None:
    global _store
    _store = Store()
