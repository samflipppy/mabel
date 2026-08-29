"""The outbound webhook. Zapier, Make, or whatever they already use.

The escape hatch: a contractor whose field software we do not integrate with
can still get his leads somewhere useful. 00-STACK.md schedules it for v2.1,
and it is here early because it costs almost nothing and covers every provider
we will never build.

**We sign what we send.** HMAC-SHA256 over `{timestamp}.{body}`, the same
construction we verify from xAI — so a customer's endpoint can tell our
requests from anybody else's. The signing secret is generated per integration
and lives in the vault.

**We do not follow redirects and we refuse private addresses.** A webhook URL
is attacker-controllable in the sense that a customer types it, and a URL
pointing at `169.254.169.254` or `localhost` turns our worker into a proxy into
our own network. That is SSRF, and this is where it gets stopped.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import socket
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from mabel_integrations.base import (
    Credentials,
    LeadPayload,
    Provider,
    PushResult,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# Their endpoint may be slow. It may not be slow enough to hold a worker.
MAX_ATTEMPT_SECONDS = 10


class UnsafeWebhookUrl(ValueError):
    """The URL points somewhere we will not send a request."""


def assert_safe_url(url: str) -> None:
    """Refuse anything that could reach our own network.

    Checked at save time and again at send time. At save time so the customer
    gets a useful error; at send time because DNS can change between the two,
    which is the whole trick behind a DNS-rebinding SSRF.
    """
    parsed = urlparse(url)

    if parsed.scheme != "https":
        # Plaintext would put a customer's lead data on the wire in the clear.
        raise UnsafeWebhookUrl("the URL must be https")

    if not parsed.hostname:
        raise UnsafeWebhookUrl("no host in that URL")

    try:
        resolved = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise UnsafeWebhookUrl(f"could not resolve {parsed.hostname}") from exc

    for entry in resolved:
        address = ipaddress.ip_address(entry[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
        ):
            # 169.254.169.254 is the cloud metadata endpoint. localhost is our
            # own API. Neither is a customer's Zapier hook.
            raise UnsafeWebhookUrl(
                f"{parsed.hostname} resolves to a private address; "
                "webhooks must point at a public endpoint"
            )


def sign(secret: str, body: bytes, *, timestamp: int | None = None) -> tuple[str, str]:
    """Sign an outbound payload. Returns `(timestamp, signature)`.

    Same construction we verify from xAI, so anybody implementing a receiver
    can follow the Standard Webhooks documentation rather than ours.
    """
    sent_at = timestamp if timestamp is not None else int(time.time())
    digest = hmac.new(secret.encode(), f"{sent_at}.".encode() + body, hashlib.sha256).hexdigest()
    return str(sent_at), f"v1,{digest}"


@dataclass(frozen=True, slots=True)
class WebhookConfig:
    url: str
    secret: str


class OutboundWebhook:
    provider = Provider.WEBHOOK

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            transport=transport,
            # Not following redirects is part of the SSRF defence: a public URL
            # that 302s to 169.254.169.254 would otherwise walk straight past
            # the check above.
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def push_lead(self, lead: LeadPayload, credentials: Credentials) -> PushResult:
        """`credentials.access_token` carries the config as `url|secret`.

        Reusing the credentials slot keeps every integration behind one
        interface, and keeps the signing secret in the vault with all the
        others rather than in a column of our own.
        """
        url, _, secret = credentials.access_token.partition("|")
        if not url or not secret:
            return PushResult(ok=False, error="webhook is not configured")

        return await self.send(WebhookConfig(url=url, secret=secret), lead)

    async def send(self, config: WebhookConfig, lead: LeadPayload) -> PushResult:
        import json

        try:
            # Re-checked here, not just at save time: DNS can change between
            # the two, which is exactly the DNS-rebinding trick.
            assert_safe_url(config.url)
        except UnsafeWebhookUrl as exc:
            return PushResult(ok=False, error=str(exc))

        payload = {
            "event": "lead.created",
            "lead": {
                "id": str(lead.lead_id),
                "name": lead.caller_name,
                "phone": lead.phone_e164,
                "address": lead.address,
                "job_type": lead.job_type,
                "description": lead.description,
                "urgency": lead.urgency,
                "source": lead.source,
                "created_at": lead.created_at.isoformat(),
            },
        }
        # Serialise once. The bytes we sign have to be the bytes we send, for
        # the same reason we verify inbound signatures against a raw body.
        body = json.dumps(payload, separators=(",", ":")).encode()
        timestamp, signature = sign(config.secret, body)

        try:
            response = await self._client.post(
                config.url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "webhook-timestamp": timestamp,
                    "webhook-signature": signature,
                    "user-agent": "Mabel/2.0",
                },
            )
        except httpx.HTTPError as exc:
            return PushResult(ok=False, error=f"unreachable: {type(exc).__name__}", payload=payload)

        if response.status_code >= 400:
            return PushResult(
                ok=False,
                error=f"endpoint returned {response.status_code}",
                payload=payload,
            )

        if 300 <= response.status_code < 400:
            # We do not follow it, so say why rather than reporting success.
            return PushResult(
                ok=False,
                error=f"endpoint redirected ({response.status_code}); give us the final URL",
                payload=payload,
            )

        return PushResult(ok=True, external_ref=None, payload=payload)


def generate_secret() -> str:
    """A signing secret for a new webhook integration.

    Generated by us rather than chosen by the customer: a secret somebody types
    is a secret somebody reuses.
    """
    import secrets

    return f"whsec_{secrets.token_urlsafe(32)}"
