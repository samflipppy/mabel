"""Tenant is resolved from the inbound DID, never from a model argument."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


class UnknownDidError(LookupError):
    """This number is not one of ours."""


@dataclass(frozen=True)
class Tenant:
    id: UUID
    vertical: str
    name: str


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


def normalize_e164(raw: str) -> str:
    text = raw.strip()
    if "sip:" in text.casefold():
        # <sip:+18005550199@sip.voice.x.ai>;transport=tls
        start = text.casefold().find("sip:") + 4
        rest = text[start:]
        for end_token in (">", ";", "@"):
            cut = rest.find(end_token)
            if cut != -1:
                rest = rest[:cut]
                break
        text = rest
    digits = "".join(ch for ch in text if ch.isdigit() or ch == "+")
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if digits and not digits.startswith("+"):
        digits = "+" + digits
    return digits


_directory = MemoryDidDirectory()


def directory() -> MemoryDidDirectory:
    return _directory


def reset_directory() -> None:
    global _directory
    _directory = MemoryDidDirectory()
