"""What every integration has in common.

Four providers, four different APIs, and one set of rules that applies to all
of them:

**We never hold a tenant's credentials.** The OAuth token lives in the Supabase
vault and `integrations.vault_key` holds the key name. The domain model already
refuses to persist anything token-shaped in `integrations.config`; this is the
other half — a `Credentials` object is built from a vault read at the moment it
is needed and is never written anywhere.

**A push is recorded whether it worked or not.** `integration_events` gets a
row either way. An integration that silently stops working is worse than one
that visibly fails, because the contractor keeps believing his leads are
arriving in Jobber.

**A failure never loses the lead.** Every push happens after the lead is
already committed. The worst outcome is a lead in Mabel and not in Jobber,
which is recoverable; the unacceptable one is a lead nowhere.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

logger = logging.getLogger(__name__)


class Provider(StrEnum):
    """Mirrors the CHECK constraint on `integrations.provider`."""

    GOOGLE_CALENDAR = "google_calendar"
    JOBBER = "jobber"
    HOUSECALL_PRO = "housecall_pro"
    WEBHOOK = "webhook"


class IntegrationUnavailable(RuntimeError):
    """No credentials for this provider. See docs/BLOCKED.md #10-#12."""


class IntegrationError(RuntimeError):
    """The provider answered, and the answer was not usable."""


class VaultUnavailable(IntegrationUnavailable):
    """Supabase vault is not configured, so a stored token cannot be read.

    We do not fall back to reading a token from `integrations.config`. That
    column is checked by the domain model precisely so no token is ever there,
    and a fallback would make the check pointless.
    """


@dataclass(frozen=True, slots=True)
class Credentials:
    """Held in memory, for one operation, and never written down."""

    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    account_id: str | None = None

    def __repr__(self) -> str:
        # A token in a traceback ends up in Sentry, in a log aggregator, and in
        # a screenshot in a support thread.
        return f"Credentials(account_id={self.account_id!r}, token=<redacted>)"

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        from datetime import UTC
        from datetime import datetime as dt

        return self.expires_at <= dt.now(UTC)


@dataclass(frozen=True, slots=True)
class LeadPayload:
    """What every integration receives. Deliberately no money field.

    A lead pushed to Jobber carries what the caller said. Its value is
    owner-entered, lives in Mabel, and is not ours to guess into somebody
    else's system as an estimate.
    """

    lead_id: UUID
    caller_name: str | None
    phone_e164: str | None
    address: str | None
    job_type: str | None
    description: str | None
    urgency: str
    source: str | None
    created_at: datetime

    def summary(self) -> str:
        parts = [self.job_type or "After-hours call"]
        if self.urgency == "emergency":
            parts.insert(0, "EMERGENCY")
        return " - ".join(parts)


@dataclass(frozen=True, slots=True)
class PushResult:
    """Recorded in `integration_events` whether it worked or not."""

    ok: bool
    external_ref: str | None = None
    error: str | None = None
    # What actually went, for the audit trail. Never contains a credential.
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        return "ok" if self.ok else "failed"


class Integration(Protocol):
    """The shape every provider implements."""

    provider: Provider

    async def push_lead(self, lead: LeadPayload, credentials: Credentials) -> PushResult: ...

    async def aclose(self) -> None: ...


def oauth_client_id(provider: Provider) -> str | None:
    """From the environment. None when not configured, so the Integrations
    screen can say which ones are actually connectable."""
    return os.environ.get(f"{provider.value.upper()}_CLIENT_ID")


def oauth_client_secret(provider: Provider) -> str:
    secret = os.environ.get(f"{provider.value.upper()}_CLIENT_SECRET")
    if not secret:
        raise IntegrationUnavailable(
            f"{provider.value.upper()}_CLIENT_SECRET is unset. This integration "
            "cannot be connected. See docs/BLOCKED.md."
        )
    return secret


def is_connectable(provider: Provider) -> bool:
    return oauth_client_id(provider) is not None


async def read_credentials(vault_key: str | None) -> Credentials:
    """Read a token out of the Supabase vault.

    Fails closed and specifically. The vault does not exist yet
    (docs/BLOCKED.md #14), and the alternative — storing tokens in a column we
    control — is the thing the schema comment and the domain validator both
    exist to prevent.
    """
    if not vault_key:
        raise VaultUnavailable("this integration has no vault key")

    if not os.environ.get("SUPABASE_VAULT_URL"):
        raise VaultUnavailable(
            "Supabase vault is not configured, so the stored token cannot be read. "
            "Tokens are never kept in integrations.config. See docs/BLOCKED.md #14."
        )

    # TODO(supabase): read `vault_key` from the vault and build Credentials.
    # Deliberately not implemented against a guess: getting this wrong means
    # either a token in the wrong place or a silent failure to read one.
    raise VaultUnavailable("vault reads are not implemented yet")


def redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip anything credential-shaped before a payload is stored.

    `integration_events.payload` is an audit trail somebody will read in a
    support thread, and a bearer token in it is a bearer token in a screenshot.
    """
    forbidden = {
        "access_token",
        "refresh_token",
        "authorization",
        "client_secret",
        "api_key",
        "token",
        "password",
    }
    return {
        key: ("<redacted>" if key.lower() in forbidden else value) for key, value in payload.items()
    }
