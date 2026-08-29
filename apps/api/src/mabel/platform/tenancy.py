"""Tenant is resolved from the inbound DID, never from a model argument.

When DATABASE_URL is set, resolve via app.resolve_tenant_from_did (SECURITY DEFINER),
then load that tenant's shop packet inside tenant_scope. Memory directory stays for
unit tests. Unknown DID fails closed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from mabel.platform.phones import normalize_e164
from mabel.shops.packet import ShopPacket


class UnknownDidError(LookupError):
    """This number is not one of ours."""


class DuplicateDidError(ValueError):
    """This inbound number already belongs to a shop."""


@dataclass(frozen=True)
class Tenant:
    id: UUID
    vertical: str
    name: str
    packet: ShopPacket | None = None
    status: str = "draft"


class DidDirectory:
    def resolve(self, e164: str) -> Tenant:
        raise NotImplementedError


class MemoryDidDirectory(DidDirectory):
    def __init__(self, mapping: dict[str, Tenant] | None = None) -> None:
        self._mapping = mapping or {}

    def register(self, e164: str, tenant: Tenant) -> None:
        self._mapping[normalize_e164(e164)] = tenant

    def resolve(self, e164: str) -> Tenant:
        tenant = self._mapping.get(normalize_e164(e164))
        if tenant is None:
            raise UnknownDidError("Mabel does not know this number.")
        return tenant

    def did_for(self, tenant_id: UUID) -> str | None:
        for did, tenant in self._mapping.items():
            if tenant.id == tenant_id:
                return did
        return None


class PostgresDidDirectory(DidDirectory):
    """DID lookup through Postgres, then the shop packet under tenant_scope."""

    def __init__(self, conn: Any | None = None, *, database_url: str | None = None) -> None:
        self._conn = conn
        self._database_url = database_url

    def resolve(self, e164: str) -> Tenant:
        from mabel.platform.db import connect, tenant_scope
        from mabel.shops.store import fetch_shop_packet

        did = normalize_e164(e164)
        if self._conn is not None:
            tenant_id = _lookup_did(self._conn, did)
            with tenant_scope(tenant_id, self._conn):
                packet = fetch_shop_packet(self._conn, tenant_id)
            return _tenant_from_packet(packet)

        lookup = connect(self._database_url)
        try:
            tenant_id = _lookup_did(lookup, did)
        finally:
            lookup.close()

        with tenant_scope(tenant_id, database_url=self._database_url) as scoped:
            packet = fetch_shop_packet(scoped, tenant_id)
        return _tenant_from_packet(packet)


def _lookup_did(conn: Any, did: str) -> UUID:
    row = conn.execute("SELECT app.resolve_tenant_from_did(%s)", (did,)).fetchone()
    if not row or row[0] is None:
        raise UnknownDidError("Mabel does not know this number.")
    return UUID(str(row[0]))


def _tenant_from_packet(packet: ShopPacket) -> Tenant:
    return Tenant(id=packet.tenant_id, vertical=packet.vertical, name=packet.name, packet=packet)


def _database_url() -> str | None:
    value = os.environ.get("DATABASE_URL")
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


_directory = MemoryDidDirectory()


def directory() -> DidDirectory:
    if _database_url():
        return PostgresDidDirectory()
    return _directory


def reset_directory() -> None:
    global _directory
    _directory = MemoryDidDirectory()
