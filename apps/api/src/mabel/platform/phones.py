"""E.164 helpers. Tenant identity still comes from the inbound DID, never a model argument."""

from __future__ import annotations

import re

E164_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")


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
