#!/usr/bin/env python3
"""Compare GitHub branch protection with the versioned release policy."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


def _load_remote(repository: str, branch: str, token: str) -> dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "botparty-repository-policy/1",
    }

    def get(path: str) -> Any:
        request = Request(f"https://api.github.com/repos/{repository}{path}", headers=headers)
        with urlopen(request, timeout=15) as response:
            return json.load(response)

    protection = get(f"/branches/{branch}/protection")
    signed = get(f"/branches/{branch}/protection/required_signatures")
    protection["required_signatures"] = signed
    return protection


def _enabled(value: Any) -> bool:
    return isinstance(value, dict) and value.get("enabled") is True


def validate(policy: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    checks = actual.get("required_status_checks") or {}
    contexts = set(checks.get("contexts") or [])
    contexts.update(item.get("context", "") for item in checks.get("checks") or [])
    missing = sorted(set(policy["requiredChecks"]) - contexts)
    if missing:
        failures.append(f"missing required checks: {', '.join(missing)}")
    reviews = actual.get("required_pull_request_reviews") or {}
    if int(reviews.get("required_approving_review_count", 0)) < int(
        policy["requiredApprovingReviews"]
    ):
        failures.append("not enough required approving reviews")
    if policy["requireConversationResolution"] and not _enabled(
        actual.get("required_conversation_resolution")
    ):
        failures.append("conversation resolution is not required")
    if policy["requireSignedCommits"] and not _enabled(actual.get("required_signatures")):
        failures.append("signed commits are not required")
    if policy["enforceAdmins"] and not _enabled(actual.get("enforce_admins")):
        failures.append("administrators can bypass protection")
    if not policy["allowForcePushes"] and _enabled(actual.get("allow_force_pushes")):
        failures.append("force pushes are allowed")
    if not policy["allowDeletions"] and _enabled(actual.get("allow_deletions")):
        failures.append("branch deletion is allowed")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=Path(".github/repository-policy.json"))
    parser.add_argument("--actual", type=Path)
    parser.add_argument("--repository", default=os.getenv("GITHUB_REPOSITORY", ""))
    args = parser.parse_args()
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    if args.actual:
        actual = json.loads(args.actual.read_text(encoding="utf-8"))
    else:
        token = os.getenv("GITHUB_TOKEN", "")
        if not args.repository or not token:
            parser.error("--actual or GITHUB_REPOSITORY and GITHUB_TOKEN are required")
        actual = _load_remote(args.repository, policy["branch"], token)
    failures = validate(policy, actual)
    if failures:
        print("\n".join(failures))
        return 1
    print(f"repository policy for {policy['branch']} is satisfied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
