"""Owner texts. Emergencies now, everything else at 7am local.

Never send if Telnyx is missing. Never log a key.
"""

from __future__ import annotations

from mabel.platform.config import ConfigError, telnyx_ready


class SmsError(RuntimeError):
    pass


def notify_owner(*, body: str, to: str | None = None) -> dict[str, str]:
    if not telnyx_ready():
        raise SmsError("Mabel cannot text the owner. Telnyx is not configured.")
    if not body.strip():
        raise SmsError("Mabel will not send an empty text.")
    # Sending is not wired. A missing key already failed closed above.
    raise SmsError("Mabel is not sending texts from this stub.")
