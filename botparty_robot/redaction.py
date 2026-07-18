"""Central structured and textual secret redaction."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

SECRET_PLACEHOLDER = "${SECRET}"

_SECRET_KEY_PARTS = frozenset(
    {
        "apikey",
        "authorization",
        "bearer",
        "clientsecret",
        "cookie",
        "credential",
        "devicekey",
        "password",
        "passphrase",
        "privatekey",
        "refreshtoken",
        "secret",
        "sessionkey",
        "token",
        "webhook",
    }
)
_TEXT_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;\]\}]+"),
    re.compile(
        r"(?i)((?:claim|auth|device|refresh|access|session)[_-]?token\s*[:=]\s*)"
        r"[^\s,;\]\}]+"
    ),
    re.compile(
        r"(?i)((?:api|secret|access|private|client)[_-]?key\s*[:=]\s*)"
        r"[^\s,;\]\}]+"
    ),
    re.compile(r"(?i)((?:password|passphrase|credential)\s*[:=]\s*)[^\s,;\]\}]+"),
    re.compile(r"(?i)(https?://)[^/@\s]+@"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?\b"),
    re.compile(
        r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----"
    ),
)


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def is_secret_key(value: object) -> bool:
    normalized = _normalized_key(value)
    if normalized.endswith(("file", "filename", "path", "directory")):
        return False
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def _redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.scheme or not parsed.hostname or not (parsed.username or parsed.password):
        return value
    host = parsed.hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit(
        (parsed.scheme, f"[REDACTED]@{host}", parsed.path, parsed.query, parsed.fragment)
    )


def _redact_all_leaves(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _redact_all_leaves(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_redact_all_leaves(item) for item in value]
    if value is None:
        return None
    return SECRET_PLACEHOLDER


def redact_structure(value: Any) -> Any:
    """Return a deep, JSON-compatible copy with credentials removed.

    Built-in option dictionaries retain non-secret values. Custom adapters and
    providers are treated conservatively because their schemas are not owned by
    this package: every custom option leaf is masked.
    """

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        profile_type = str(value.get("type", "")).strip().lower()
        for key, item in value.items():
            output_key = str(key)
            if is_secret_key(output_key):
                result[output_key] = None if item is None else SECRET_PLACEHOLDER
            elif output_key == "options" and profile_type == "custom":
                result[output_key] = _redact_all_leaves(item)
            else:
                result[output_key] = redact_structure(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_structure(item) for item in value]
    if isinstance(value, str):
        return _redact_url(value)
    return copy.deepcopy(value)


def redact_text(value: str, literals: tuple[str, ...] = ()) -> str:
    redacted = value
    for pattern in _TEXT_PATTERNS:
        if "PRIVATE KEY" in pattern.pattern:
            redacted = pattern.sub("[REDACTED_PRIVATE_KEY]", redacted)
        elif pattern.pattern.startswith("\\bAKIA"):
            redacted = pattern.sub("[REDACTED_AWS_KEY]", redacted)
        elif pattern.pattern.startswith("\\bgh"):
            redacted = pattern.sub("[REDACTED_GITHUB_TOKEN]", redacted)
        elif pattern.pattern.startswith("\\bxox"):
            redacted = pattern.sub("[REDACTED_SLACK_TOKEN]", redacted)
        elif pattern.pattern.startswith("\\beyJ"):
            redacted = pattern.sub("[REDACTED_JWT]", redacted)
        elif "https?" in pattern.pattern:
            redacted = pattern.sub(r"\1[REDACTED]@", redacted)
        else:
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
    for literal in literals:
        if literal:
            redacted = re.sub(
                re.escape(literal),
                "[REDACTED_OPERATOR_TERM]",
                redacted,
                flags=re.I,
            )
    return redacted
