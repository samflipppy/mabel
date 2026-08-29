from __future__ import annotations

from mabel.platform.phones import normalize_e164


def sip_header(headers: list[dict[str, str]], name: str) -> str | None:
    want = name.casefold()
    for item in headers:
        if str(item.get("name", "")).casefold() == want:
            return str(item.get("value", ""))
    return None


def to_did_from_payload(payload: dict) -> str:
    data = payload.get("data") or {}
    headers = data.get("sip_headers") or []
    raw = sip_header(headers, "To")
    if not raw:
        raise ValueError("Mabel cannot find the number this call came in on.")
    return normalize_e164(raw)
