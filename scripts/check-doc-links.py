#!/usr/bin/env python3
"""Validate Markdown file, anchor and optionally external links."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")


def github_slug(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value).strip().lower()
    value = re.sub(r"[^\w\- ]", "", value, flags=re.UNICODE)
    return re.sub(r"\s", "-", value)


def anchors(document: Path) -> set[str]:
    found: set[str] = set()
    counts: dict[str, int] = {}
    for line in document.read_text(encoding="utf-8").splitlines():
        match = HEADING.match(line)
        if not match:
            continue
        base = github_slug(match.group(1))
        duplicate = counts.get(base, 0)
        counts[base] = duplicate + 1
        found.add(base if duplicate == 0 else f"{base}-{duplicate}")
    return found


def check_external(url: str, timeout: float) -> str | None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        return "unsupported external URL"
    request = Request(url, headers={"User-Agent": "botparty-doc-link-check/1"}, method="HEAD")
    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
    except HTTPError as exc:
        if exc.code in {403, 405}:
            try:
                with urlopen(
                    Request(url, headers={"User-Agent": "botparty-doc-link-check/1"}),
                    timeout=timeout,
                ) as response:
                    status = response.status
            except (HTTPError, URLError, TimeoutError) as retry_exc:
                return f"external error: {type(retry_exc).__name__}"
        else:
            return f"external HTTP {exc.code}"
    except (URLError, TimeoutError) as exc:
        return f"external unavailable: {type(exc).__name__}"
    return None if 200 <= status < 400 else f"external HTTP {status}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--external", action="store_true")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("documents", nargs="*", type=Path)
    args = parser.parse_args(argv)
    failures: list[str] = []
    checked_external: dict[str, str | None] = {}
    documents = args.documents or [Path("README.md"), *Path("docs").rglob("*.md")]
    for document in documents:
        for line_number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1):
            for raw_target in LINK.findall(line):
                raw_target = raw_target.strip().split(" ", 1)[0]
                if raw_target.startswith("mailto:"):
                    continue
                if "://" in raw_target:
                    if args.external:
                        checked_external.setdefault(
                            raw_target, check_external(raw_target, args.timeout)
                        )
                        error = checked_external[raw_target]
                        if error:
                            failures.append(f"{document}:{line_number}: {error}: {raw_target}")
                    continue
                target, separator, anchor = raw_target.partition("#")
                target = target.strip()
                resolved = document if not target else (document.parent / unquote(target)).resolve()
                if not target and not separator:
                    continue
                if not resolved.exists():
                    failures.append(f"{document}:{line_number}: missing {target}")
                    continue
                if anchor and resolved.suffix.lower() == ".md":
                    expected = unquote(anchor).lower()
                    if expected not in anchors(resolved):
                        failures.append(
                            f"{document}:{line_number}: missing anchor #{expected} in {resolved}"
                        )
    if failures:
        print("\n".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
