#!/usr/bin/env python3
"""Validate deployment-owned privacy and consent release evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

PLACEHOLDER_MARKERS = ("REPLACE", "TODO", "EXAMPLE", "CHANGEME")


def _meaningful_string(value: object, *, maximum: int = 2_048) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= maximum
        and not any(marker in value.upper() for marker in PLACEHOLDER_MARKERS)
    )


def _parse_utc(value: object) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone is required")
    return parsed.astimezone(dt.timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    try:
        payload = json.loads(args.evidence.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"invalid compliance evidence: {exc}\n")
        return 1
    errors: list[str] = []
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        errors.append("unsupported compliance evidence schema")
        payload = {}
    for field in ("policyVersion", "approvedBy", "approvedAt", "expiresAt"):
        value = payload.get(field)
        if not _meaningful_string(value):
            errors.append(f"compliance evidence requires {field}")
    try:
        approved = _parse_utc(payload.get("approvedAt"))
        expires = _parse_utc(payload.get("expiresAt"))
        now = dt.datetime.now(dt.timezone.utc)
        if approved > now:
            errors.append("compliance approval cannot be in the future")
        if expires <= now:
            errors.append("compliance evidence is expired")
        if expires <= approved:
            errors.append("compliance expiry must follow approval")
    except ValueError:
        errors.append("approvedAt and expiresAt must be timezone-aware ISO-8601")
    deployments = payload.get("deployments")
    if not isinstance(deployments, list) or not deployments:
        errors.append("at least one deployment compliance record is required")
        deployments = []
    deployment_ids: set[str] = set()
    for index, deployment in enumerate(deployments):
        if not isinstance(deployment, dict):
            errors.append(f"deployment {index} is invalid")
            continue
        for field in (
            "deploymentId",
            "controller",
            "region",
            "legalBasis",
            "deletionProcedure",
            "exportProcedure",
            "testRunId",
        ):
            value = deployment.get(field)
            if not _meaningful_string(value):
                errors.append(f"deployment {index} requires {field}")
        deployment_id = deployment.get("deploymentId")
        if isinstance(deployment_id, str):
            if deployment_id in deployment_ids:
                errors.append(f"deployment {index} duplicates deploymentId")
            deployment_ids.add(deployment_id)
        retention = deployment.get("retentionDays")
        if (
            isinstance(retention, bool)
            or not isinstance(retention, int)
            or not 0 <= retention <= 3650
        ):
            errors.append(f"deployment {index} has invalid retentionDays")
        processors = deployment.get("processors")
        if not isinstance(processors, list) or any(
            not _meaningful_string(processor) for processor in processors
        ):
            errors.append(f"deployment {index} requires a processor inventory")
        consent = deployment.get("consentTests")
        if not isinstance(consent, dict) or any(
            consent.get(check) is not True for check in ("optOut", "export", "deletion")
        ):
            errors.append(f"deployment {index} has incomplete consent/data-subject tests")
    if errors:
        sys.stderr.write("\n".join(errors) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
