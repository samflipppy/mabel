"""Jobber. Push leads as Requests.

04-REPO.md's Phase 6 bar: "a lead lands in a customer's Jobber without anyone
touching it."

Jobber's API is GraphQL, which shapes this file: one endpoint, mutations as
strings, and errors that arrive with a 200 status in an `errors` array rather
than as an HTTP failure. Checking only the status code would report every
failure as a success, which is exactly the silent-integration-failure this
package is written to avoid.

Needs a Jobber developer app that does not exist yet (docs/BLOCKED.md #11).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from mabel_integrations.base import (
    Credentials,
    LeadPayload,
    Provider,
    PushResult,
    redact,
)

logger = logging.getLogger(__name__)

API_URL = "https://api.getjobber.com/api/graphql"
DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

# Jobber pins its GraphQL schema by date and breaks without one.
API_VERSION = "2024-06-10"

SCOPES = ("read_clients", "write_clients", "write_requests")

CREATE_REQUEST = """
mutation CreateRequest($input: RequestCreateInput!) {
  requestCreate(input: $input) {
    request { id title }
    userErrors { message path }
  }
}
"""

FIND_CLIENT = """
query FindClient($phone: String!) {
  clients(filter: { searchTerm: $phone }, first: 1) {
    nodes { id name }
  }
}
"""

CREATE_CLIENT = """
mutation CreateClient($input: ClientCreateInput!) {
  clientCreate(input: $input) {
    client { id }
    userErrors { message path }
  }
}
"""


class Jobber:
    provider = Provider.JOBBER

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def push_lead(self, lead: LeadPayload, credentials: Credentials) -> PushResult:
        """Find or create the client, then create the Request.

        Two steps because a Request needs a client, and a contractor's existing
        customer should not become a duplicate because they rang after hours.
        Matching is on phone number, deterministically — the same rule Mabel's
        own contact resolution uses, and for the same reason.
        """
        try:
            client_id, why = await self._resolve_client(lead, credentials)
        except Exception as exc:  # noqa: BLE001 - reported, never raised past here
            logger.warning("jobber client lookup failed: %s", exc)
            return PushResult(ok=False, error=f"client lookup failed: {type(exc).__name__}")

        if client_id is None:
            # `why` carries what Jobber actually said. Flattening it to
            # "could not find or create the client" would hide a bad token
            # behind something that reads like a data problem.
            return PushResult(ok=False, error=why or "could not find or create the client")

        variables = {
            "input": {
                "clientId": client_id,
                "title": lead.summary(),
                # What the caller said, and nothing about money. A job value is
                # owner-entered and is not ours to push into his system as
                # though it were an estimate.
                "instructions": "\n".join(
                    filter(
                        None,
                        [
                            lead.description,
                            f"Address: {lead.address}" if lead.address else None,
                            f"Heard about you via: {lead.source}" if lead.source else None,
                            f"Taken by Mabel at {lead.created_at:%-d %b %H:%M}"
                            if _supports_dash_d()
                            else f"Taken by Mabel at {lead.created_at:%d %b %H:%M}",
                        ],
                    )
                ),
            }
        }

        result = await self._graphql(CREATE_REQUEST, variables, credentials)
        if not result.ok:
            return result

        payload = result.payload.get("data", {}).get("requestCreate", {})
        errors = payload.get("userErrors") or []
        if errors:
            # GraphQL user errors arrive with a 200. Treating them as success
            # is the silent failure this whole file is arranged to avoid.
            return PushResult(
                ok=False,
                error="; ".join(str(error.get("message")) for error in errors)[:300],
                payload=redact(variables),
            )

        request = payload.get("request") or {}
        return PushResult(ok=True, external_ref=str(request.get("id")), payload=redact(variables))

    async def _resolve_client(
        self, lead: LeadPayload, credentials: Credentials
    ) -> tuple[str | None, str | None]:
        """Returns `(client_id, why_not)`. Exactly one is ever set."""
        if lead.phone_e164:
            found = await self._graphql(FIND_CLIENT, {"phone": lead.phone_e164}, credentials)
            if not found.ok:
                # A failed lookup is usually a bad token or an outage, not a
                # missing client. Creating one on top of that would be wrong.
                return None, found.error
            nodes = found.payload.get("data", {}).get("clients", {}).get("nodes") or []
            if nodes:
                return str(nodes[0]["id"]), None

        name = lead.caller_name or "After-hours caller"
        first, _, last = name.partition(" ")
        created = await self._graphql(
            CREATE_CLIENT,
            {
                "input": {
                    "firstName": first,
                    "lastName": last or "",
                    "phones": (
                        [{"number": lead.phone_e164, "primary": True}] if lead.phone_e164 else []
                    ),
                }
            },
            credentials,
        )
        if not created.ok:
            return None, created.error

        payload = created.payload.get("data", {}).get("clientCreate", {})
        errors = payload.get("userErrors") or []
        if errors:
            return None, "; ".join(str(error.get("message")) for error in errors)[:300]

        client_id = str((payload.get("client") or {}).get("id") or "")
        return (client_id or None), (None if client_id else "Jobber created no client")

    async def _graphql(
        self, query: str, variables: dict[str, Any], credentials: Credentials
    ) -> PushResult:
        try:
            response = await self._client.post(
                API_URL,
                headers={
                    "Authorization": f"Bearer {credentials.access_token}",
                    "X-JOBBER-GRAPHQL-VERSION": API_VERSION,
                },
                json={"query": query, "variables": variables},
            )
        except httpx.HTTPError as exc:
            return PushResult(ok=False, error=f"unreachable: {exc}")

        if response.status_code >= 400:
            return PushResult(ok=False, error=f"jobber returned {response.status_code}")

        body = response.json()
        if body.get("errors"):
            # Transport-level GraphQL errors, also on a 200.
            return PushResult(
                ok=False,
                error="; ".join(str(error.get("message")) for error in body["errors"])[:300],
            )
        return PushResult(ok=True, payload=body)


def _supports_dash_d() -> bool:
    """`%-d` is a glibc extension and raises on Windows. The tests run on both,
    and a ValueError formatting a date would fail the whole push."""
    from datetime import datetime as dt

    try:
        dt(2026, 1, 1).strftime("%-d")
    except ValueError:
        return False
    return True
