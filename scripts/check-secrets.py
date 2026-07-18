#!/usr/bin/env python3
"""Fail on high-confidence secrets in version-controlled files."""

from __future__ import annotations

import argparse
import math
import re
import subprocess
from pathlib import Path

PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "JWT or BotParty claim": re.compile(
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?:\.[A-Za-z0-9_-]{8,})?\b"
    ),
    "Google service account": re.compile(r'"type"\s*:\s*"service_account"'),
    "Google private key id": re.compile(r'"private_key_id"\s*:\s*"[a-fA-F0-9]{20,}"'),
    "Stripe secret": re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{20,}\b"),
    "provider API key": re.compile(
        r"(?i)(?:api[_-]?key|client[_-]?secret|claim[_-]?token|access[_-]?token)"
        r"\s*[:=]\s*(?:[\"'][A-Za-z0-9_./+=-]{20,}[\"']|"
        r"[A-Za-z0-9_./+=-]{20,}\s*(?:#.*)?$)"
    ),
}

IGNORED_VALUES = ("replace-with", "PASTE_YOUR", "example", "test-claim-token")
HIGH_ENTROPY = re.compile(r"(?:=|:)\s*[\"']?([A-Za-z0-9_+/=]{32,})")


def _entropy(value: str) -> float:
    frequencies = {character: value.count(character) for character in set(value)}
    return -sum(
        (count / len(value)) * math.log2(count / len(value)) for count in frequencies.values()
    )


def _obvious_fixture_match(name: str, matched: str) -> bool:
    """Ignore only deterministic low-entropy documentation/test placeholders."""

    value = re.split(r"[:=]", matched, maxsplit=1)[-1].strip(" \t\"'")
    if name == "AWS access key":
        return _entropy(value) < 2.0
    return (
        name == "provider API key"
        and not any(character.isdigit() for character in value)
        and re.fullmatch(r"[A-Za-z_-]+", value) is not None
        and value.count("_") + value.count("-") >= 2
    )


def scan_text(text: str, source: str) -> list[str]:
    findings: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if "secret-scan: allow-test-fixture" in line or any(
            ignored.lower() in line.lower() for ignored in IGNORED_VALUES
        ):
            continue
        for name, pattern in PATTERNS.items():
            match = pattern.search(line)
            if match and not _obvious_fixture_match(name, match.group(0)):
                findings.append(f"{source}:{line_number}: {name}")
        lower = line.lower()
        if not any(marker in lower for marker in ("sha256", "digest", "checksum", "commit")):
            for candidate in HIGH_ENTROPY.findall(line):
                character_classes = sum(
                    (
                        any(char.islower() for char in candidate),
                        any(char.isupper() for char in candidate),
                        any(char.isdigit() for char in candidate),
                    )
                )
                if character_classes >= 3 and _entropy(candidate) >= 4.2:
                    findings.append(f"{source}:{line_number}: generic high-entropy value")
                    break
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        check=True,
        capture_output=True,
    )
    findings: list[str] = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = Path(raw_path.decode("utf-8"))
        try:
            data = path.read_bytes()
            if b"\0" in data:
                continue
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend(scan_text(text, str(path)))
    if args.history:
        history = subprocess.run(
            ["git", "log", "--all", "--format=commit:%H", "-p", "--no-ext-diff", "--unified=0"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        findings.extend(scan_text(history, "git-history"))
    if findings:
        print("\n".join(findings))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
