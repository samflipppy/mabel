"""The seam between a tool handler and the database.

Handlers do three things: validate what the model passed, decide what to do,
and shape what goes back. None of that needs a database to test, and all of it
is where the interesting mistakes live — a handler that leaks a dollar figure,
or trusts an argument it should not.

So handlers depend on this protocol rather than on `packages/db` directly.
`DbRepo` is the real one and holds no logic of its own. `FakeRepo` is what the
unit tests bind. The SQL itself is exercised in `tests/isolation/`, against a
real Postgres, where RLS is doing its job.

**The connection handed to `DbRepo` is already inside `tenant_scope()`.** The
dispatcher opens it from the token's tenant before any handler runs, so a
handler cannot reach a tenant it was not given, and cannot forget to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncConnection

from mabel_db.queries import config as config_q
from mabel_db.queries import contacts as contacts_q
from mabel_db.queries import leads as leads_q


class Repo(Protocol):
    """What a tool handler is allowed to ask the world for."""

    async def find_contact_by_phone(self, phone_e164: str) -> dict[str, Any] | None: ...

    async def resolve_or_create_contact(
        self, *, phone_e164: str | None, name: str | None
    ) -> tuple[UUID, str]: ...

    async def last_job(self, contact_id: UUID) -> dict[str, Any] | None: ...

    async def job_history(self, contact_id: UUID, *, limit: int) -> list[dict[str, Any]]: ...

    async def live_config(self) -> Any | None: ...

    async def search_knowledge(self, question: str) -> list[dict[str, str]]: ...

    async def create_lead(self, **fields: Any) -> UUID: ...

    async def record_event(self, **fields: Any) -> None: ...

    async def available_slots(self, *, job_type: str) -> list[dict[str, Any]]: ...

    async def book_slot(self, *, slot_id: str, contact_id: UUID, lead_id: UUID | None) -> bool: ...

    async def notify_oncall(self, *, body: str, lead_id: UUID) -> bool: ...


@dataclass
class DbRepo:
    """The real one. A thin adapter over `packages/db/queries`, deliberately
    holding no decisions of its own — everything it does is a query."""

    conn: AsyncConnection
    tenant_id: UUID
    call_id: UUID | None = None

    async def find_contact_by_phone(self, phone_e164: str) -> dict[str, Any] | None:
        row = await contacts_q.find_by_phone(self.conn, phone_e164)
        if row is None:
            return None
        return {
            "id": row.id,
            "display_name": row.display_name,
            "primary_phone": row.primary_phone,
            "first_seen_at": row.first_seen_at,
        }

    async def resolve_or_create_contact(
        self, *, phone_e164: str | None, name: str | None
    ) -> tuple[UUID, str]:
        row, how = await contacts_q.resolve_or_create(
            self.conn, tenant_id=self.tenant_id, phone_e164=phone_e164, name=name
        )
        return row.id, how

    async def last_job(self, contact_id: UUID) -> dict[str, Any] | None:
        return await leads_q.last_job_summary(self.conn, contact_id)

    async def job_history(self, contact_id: UUID, *, limit: int) -> list[dict[str, Any]]:
        return [
            {
                "job_type": row.job_type,
                "status": row.status,
                "created_at": row.created_at,
                "urgency": row.urgency,
            }
            for row in await leads_q.job_history(self.conn, contact_id, limit=limit)
        ]

    async def live_config(self) -> Any | None:
        return await config_q.live_config(self.conn)

    async def search_knowledge(self, question: str) -> list[dict[str, str]]:
        return [
            {"question": row.question, "answer": row.answer}
            for row in await config_q.search_knowledge(self.conn, question)
        ]

    async def create_lead(self, **fields: Any) -> UUID:
        return await leads_q.create(
            self.conn, tenant_id=self.tenant_id, call_id=self.call_id, **fields
        )

    async def record_event(self, **fields: Any) -> None:
        from mabel_db.queries import events as events_q

        await events_q.append(self.conn, tenant_id=self.tenant_id, **fields)

    async def available_slots(self, *, job_type: str) -> list[dict[str, Any]]:
        from mabel_db.queries import availability as availability_q

        return await availability_q.slots(self.conn, job_type=job_type)

    async def book_slot(self, *, slot_id: str, contact_id: UUID, lead_id: UUID | None) -> bool:
        from mabel_db.queries import availability as availability_q

        return await availability_q.book(
            self.conn,
            tenant_id=self.tenant_id,
            slot_id=slot_id,
            contact_id=contact_id,
            lead_id=lead_id,
        )

    async def notify_oncall(self, *, body: str, lead_id: UUID) -> bool:
        from mabel_db.queries import notifications as notifications_q

        return await notifications_q.enqueue_emergency(
            self.conn, tenant_id=self.tenant_id, body=body, lead_id=lead_id
        )


@dataclass
class FakeRepo:
    """What unit tests bind. Records what was asked, answers from fields.

    It is deliberately not a mock library: a handler that stops calling
    something should fail a test on the recorded calls, not silently pass
    because a mock allowed anything.
    """

    contact: dict[str, Any] | None = None
    contact_id: UUID = field(default_factory=uuid4)
    contact_how: str = "created"
    last_job_row: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    config: Any | None = None
    knowledge: list[dict[str, str]] = field(default_factory=list)
    slots: list[dict[str, Any]] = field(default_factory=list)
    booked: bool = True
    notified: bool = True
    lead_id: UUID = field(default_factory=uuid4)

    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def _record(self, call: str, /, **kwargs: Any) -> None:
        # Positional-only, because `name` is a perfectly ordinary argument for
        # a handler to pass and it collided with the parameter here.
        self.calls.append((call, kwargs))

    def called(self, name: str) -> bool:
        return any(call == name for call, _ in self.calls)

    def args_for(self, name: str) -> dict[str, Any]:
        for call, kwargs in self.calls:
            if call == name:
                return kwargs
        raise AssertionError(f"{name} was never called; got {[c for c, _ in self.calls]}")

    async def find_contact_by_phone(self, phone_e164: str) -> dict[str, Any] | None:
        self._record("find_contact_by_phone", phone_e164=phone_e164)
        return self.contact

    async def resolve_or_create_contact(
        self, *, phone_e164: str | None, name: str | None
    ) -> tuple[UUID, str]:
        self._record("resolve_or_create_contact", phone_e164=phone_e164, name=name)
        return self.contact_id, self.contact_how

    async def last_job(self, contact_id: UUID) -> dict[str, Any] | None:
        self._record("last_job", contact_id=contact_id)
        return self.last_job_row

    async def job_history(self, contact_id: UUID, *, limit: int) -> list[dict[str, Any]]:
        self._record("job_history", contact_id=contact_id, limit=limit)
        return self.history

    async def live_config(self) -> Any | None:
        self._record("live_config")
        return self.config

    async def search_knowledge(self, question: str) -> list[dict[str, str]]:
        self._record("search_knowledge", question=question)
        return self.knowledge

    async def create_lead(self, **fields: Any) -> UUID:
        self._record("create_lead", **fields)
        return self.lead_id

    async def record_event(self, **fields: Any) -> None:
        self._record("record_event", **fields)

    async def available_slots(self, *, job_type: str) -> list[dict[str, Any]]:
        self._record("available_slots", job_type=job_type)
        return self.slots

    async def book_slot(self, *, slot_id: str, contact_id: UUID, lead_id: UUID | None) -> bool:
        self._record("book_slot", slot_id=slot_id, contact_id=contact_id, lead_id=lead_id)
        return self.booked

    async def notify_oncall(self, *, body: str, lead_id: UUID) -> bool:
        self._record("notify_oncall", body=body, lead_id=lead_id)
        return self.notified


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Everything a handler is given. Note what is not here: no tenant
    argument, because the tenant is already baked into `repo`."""

    repo: Repo
    call_id: str
    now: datetime
