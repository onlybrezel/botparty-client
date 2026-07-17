"""BotParty Robot Client - connect your robot to the world."""

from __future__ import annotations

import os
import re

__version__ = "0.2.0"


def _resolve_build_id() -> str:
    configured = os.getenv("BOTPARTY_BUILD_ID", "").strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}", configured):
        return configured
    return f"version-{__version__}"


__build_id__ = _resolve_build_id()
