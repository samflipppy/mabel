"""`get_service_area`.

Returns `{in_area, note}`. Out of area is not a hang-up: 03-VOICE.md says say
so politely and offer to take a message anyway. A contractor who covers one
town still wants to know that three people a month call him from the next one.
"""

from __future__ import annotations

import re
from typing import Any

from mabel_mcp.repo import ToolContext

# US five-digit, optionally with the +4. We compare on the five.
_ZIP = re.compile(r"^\s*(\d{5})(?:-\d{4})?\s*$")


async def get_service_area(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    config = await ctx.repo.live_config()
    if config is None:
        # No published config means we cannot say. Claiming "in area" here
        # would have her promise coverage nobody agreed to.
        return {
            "in_area": False,
            "note": "I'm not able to confirm the service area, but I can take a message.",
        }

    match = _ZIP.match(str(args.get("zip", "")))
    if match is None:
        return {
            "in_area": False,
            "note": _out_of_area_note(config),
            "reason": "not a recognisable ZIP code",
        }

    zip_code = match.group(1)
    configured = {str(z).strip()[:5] for z in (config.service_area_zips or [])}

    if not configured:
        # A tenant who has not listed any ZIPs covers everywhere as far as we
        # know. Refusing every caller because a config screen is blank is the
        # worse failure by a distance.
        return {"in_area": True, "note": config.service_area_note}

    if zip_code in configured:
        return {"in_area": True, "note": config.service_area_note}

    return {"in_area": False, "note": _out_of_area_note(config)}


def _out_of_area_note(config: Any) -> str:
    """The tenant's own wording if they set one, ours if not.

    02-PORTAL.md gives them an 'Out-of-area response text' field precisely so
    this sentence is theirs. Ours is the fallback and stays neutral: it does
    not promise a callback the owner never agreed to make.
    """
    configured = getattr(config, "service_area_note", None)
    if configured and configured.strip():
        return configured.strip()
    return "That's outside the usual service area, but I can still take a message."
