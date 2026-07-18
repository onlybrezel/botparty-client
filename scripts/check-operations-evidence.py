#!/usr/bin/env python3
"""Validate monitoring, backup and restore drill evidence for promotion."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

PLACEHOLDER_MARKERS = ("REPLACE", "TODO", "EXAMPLE", "CHANGEME")


def _meaningful_string(value: object, *, maximum: int = 512) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= maximum
        and not any(marker in value.upper() for marker in PLACEHOLDER_MARKERS)
    )


def _finite_positive(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
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
        report = json.loads(args.evidence.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"invalid operations evidence: {exc}\n")
        return 1
    errors: list[str] = []
    if not isinstance(report, dict) or report.get("schemaVersion") != 1:
        errors.append("unsupported operations evidence schema")
        report = {}
    for field in (
        "deploymentId",
        "reviewer",
        "observedAt",
        "alertRoute",
        "testAlertId",
        "backupId",
        "restoreDrillId",
    ):
        if not _meaningful_string(report.get(field)):
            errors.append(f"operations evidence requires {field}")
    try:
        observed = _parse_utc(report.get("observedAt"))
        age = dt.datetime.now(dt.timezone.utc) - observed
        if age < dt.timedelta(0) or age > dt.timedelta(days=90):
            errors.append("operations evidence must be no older than 90 days")
    except ValueError:
        errors.append("observedAt must be ISO-8601")
    if report.get("testAlertDelivered") is not True:
        errors.append("test alert delivery is not confirmed")
    rpo = report.get("rpoHours")
    backup_age = report.get("verifiedBackupAgeHours")
    if not _finite_positive(rpo):
        errors.append("rpoHours must be positive")
    if not _finite_positive(backup_age) or (_finite_positive(rpo) and backup_age > rpo):
        errors.append("verified backup age exceeds RPO")
    rto = report.get("rtoMinutes")
    restore = report.get("restoreDurationMinutes")
    if not _finite_positive(rto):
        errors.append("rtoMinutes must be positive")
    if not _finite_positive(restore) or (_finite_positive(rto) and restore > rto):
        errors.append("restore duration exceeds RTO")
    if report.get("offsiteCopyVerified") is not True or report.get("restoreVerified") is not True:
        errors.append("offsite copy and restore must both be verified")
    if errors:
        sys.stderr.write("\n".join(errors) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
