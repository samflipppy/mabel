"""Everything Mabel assumes about xAI.

`docs/xai_notes.md` is the ledger of what is verified and what is assumed.
Read it before changing `client.py`. The API is sparsely documented and you
will write confident, wrong code if you guess a parameter name.
"""

from __future__ import annotations

from mabel_xai.client import (
    ALLOWED_TOOLS,
    API_BASE,
    AUDIO_FORMAT,
    AUDIO_RATE,
    CONCURRENCY_ALERT_THRESHOLD,
    FORBIDDEN_MODEL_ALIAS,
    MAX_CONCURRENT_SESSIONS,
    MAX_SESSION_MINUTES,
    REALTIME_WS,
    SIP_FQDN,
    VOICE_MODEL,
    Client,
    FakeXaiClient,
    VoiceAgentTemplate,
    XaiClient,
    XaiError,
    XaiRefusedUnderTest,
    XaiUnavailable,
    api_key,
    concurrency_state,
    join_url,
    sip_uri,
)
from mabel_xai.pricing import (
    VOICE_CENTS_PER_MINUTE,
    PricingError,
    call_cost_cents,
    conversation_item_cost_cents,
    minutes_from_seconds,
    voice_cost_cents,
)
from mabel_xai.webhooks import (
    MAX_AGE_SECONDS,
    SecretUnavailable,
    SignatureMismatch,
    TimestampOutOfRange,
    WebhookError,
    WebhookHeaders,
    signed_payload,
    signing_secret,
    verify,
)

__all__ = [
    "ALLOWED_TOOLS",
    "API_BASE",
    "AUDIO_FORMAT",
    "AUDIO_RATE",
    "CONCURRENCY_ALERT_THRESHOLD",
    "FORBIDDEN_MODEL_ALIAS",
    "MAX_AGE_SECONDS",
    "MAX_CONCURRENT_SESSIONS",
    "MAX_SESSION_MINUTES",
    "REALTIME_WS",
    "SIP_FQDN",
    "VOICE_CENTS_PER_MINUTE",
    "VOICE_MODEL",
    "Client",
    "FakeXaiClient",
    "PricingError",
    "SecretUnavailable",
    "SignatureMismatch",
    "TimestampOutOfRange",
    "VoiceAgentTemplate",
    "WebhookError",
    "WebhookHeaders",
    "XaiClient",
    "XaiError",
    "XaiRefusedUnderTest",
    "XaiUnavailable",
    "api_key",
    "call_cost_cents",
    "concurrency_state",
    "conversation_item_cost_cents",
    "join_url",
    "minutes_from_seconds",
    "signed_payload",
    "signing_secret",
    "sip_uri",
    "verify",
    "voice_cost_cents",
]
