"""Inbound webhooks.

Every one follows invariant 8: verify against the raw body, reject a timestamp
older than 300 seconds, and be idempotent on the provider's event id.

Each provider gets its own module because each signs differently. xAI uses
HMAC-SHA256 over `{id}.{timestamp}.{body}`; Telnyx uses Ed25519 over
`{timestamp}|{body}`. One shared verifier with a scheme parameter is how one of
them ends up silently unverified.
"""
