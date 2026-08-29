"""Third-party integrations.

Four providers, four APIs, one set of rules. We never hold a tenant's
credentials — tokens live in the Supabase vault and we hold the key name. A
push is recorded whether it worked or not, because an integration that silently
stops working is worse than one that visibly fails. And a failure never loses
the lead: every push happens after the lead is already committed.
"""

from __future__ import annotations

from mabel_integrations.base import (
    Credentials,
    Integration,
    IntegrationError,
    IntegrationUnavailable,
    LeadPayload,
    Provider,
    PushResult,
    VaultUnavailable,
    is_connectable,
    oauth_client_id,
    oauth_client_secret,
    read_credentials,
    redact,
)
from mabel_integrations.google_calendar import BusyInterval, GoogleCalendar, free_slots
from mabel_integrations.housecall import HousecallPro, PlanNotSupported
from mabel_integrations.jobber import Jobber
from mabel_integrations.outbound_webhook import (
    OutboundWebhook,
    UnsafeWebhookUrl,
    WebhookConfig,
    assert_safe_url,
    generate_secret,
    sign,
)

__all__ = [
    "BusyInterval",
    "Credentials",
    "GoogleCalendar",
    "HousecallPro",
    "Integration",
    "IntegrationError",
    "IntegrationUnavailable",
    "Jobber",
    "LeadPayload",
    "OutboundWebhook",
    "PlanNotSupported",
    "Provider",
    "PushResult",
    "UnsafeWebhookUrl",
    "VaultUnavailable",
    "WebhookConfig",
    "assert_safe_url",
    "free_slots",
    "generate_secret",
    "is_connectable",
    "oauth_client_id",
    "oauth_client_secret",
    "read_credentials",
    "redact",
    "sign",
]
