#!/usr/bin/env python3
"""Validate attested backend, gateway and LiveKit contract-test evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

REQUIRED_SECURITY_CASES = {
    "cross_tenant_robot_denied",
    "expired_token_denied",
    "revoked_token_denied",
    "wrong_audience_denied",
    "scope_escalation_denied",
    "command_replay_deduplicated",
    "rate_limit_enforced",
    "foreign_livekit_room_denied",
    "diagnostics_cross_tenant_denied",
}
REQUIRED_RECOVERY_CASES = {
    "claim_and_control_ready",
    "track_publish_and_first_frame",
    "outbox_reconnect_delivery",
    "emergency_stop_during_disconnect",
    "action_result_idempotency",
    "token_revocation_disconnects",
    "network_flap_recovers",
    "ota_result_after_restart",
}
PLACEHOLDER_MARKERS = ("REPLACE", "TODO", "EXAMPLE", "CHANGEME")


def _meaningful_string(value: object, *, maximum: int = 512) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= maximum
        and not any(marker in value.upper() for marker in PLACEHOLDER_MARKERS)
    )


def _passed_case_ids(value: object, group: str, errors: list[str]) -> set[str]:
    if not isinstance(value, list):
        errors.append(f"{group} must be a list")
        return set()
    passed: set[str] = set()
    correlations: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"{group} case {index} is invalid")
            continue
        case_id = item.get("id")
        correlation = item.get("correlationId")
        if not _meaningful_string(case_id, maximum=96):
            errors.append(f"{group} case {index} requires a stable id")
            continue
        if not _meaningful_string(correlation, maximum=160):
            errors.append(f"{group} case {case_id} requires a correlationId")
            continue
        if case_id in passed:
            errors.append(f"{group} duplicates case id: {case_id}")
        if correlation in correlations:
            errors.append(f"{group} reuses correlationId: {correlation}")
        correlations.add(correlation)
        if item.get("passed") is not True:
            errors.append(f"{group} case did not pass: {case_id}")
            continue
        passed.add(str(case_id))
    return passed


def _parse_utc(value: object) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone is required")
    return parsed.astimezone(dt.timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--client-version", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    errors: list[str] = []
    try:
        payload = json.loads(args.evidence.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"invalid platform evidence: {exc}\n")
        return 1
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        errors.append("unsupported platform evidence schema")
        payload = {}
    try:
        contract_digest = hashlib.sha256(args.contract.read_bytes()).hexdigest()
    except OSError as exc:
        sys.stderr.write(f"invalid platform contract: {exc}\n")
        return 1
    if payload.get("contractSha256") != contract_digest:
        errors.append("platform evidence does not match the committed contract")
    if payload.get("clientVersion") != args.client_version:
        errors.append("platform evidence client version does not match the release")
    if str(payload.get("clientCommit", "")).lower() != args.commit.lower():
        errors.append("platform evidence client commit does not match the release")
    if re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", args.commit.lower()) is None:
        errors.append("release commit must be immutable")
    for field in (
        "backendVersion",
        "gatewayVersion",
        "livekitVersion",
        "stagingEnvironment",
        "testRunId",
        "workflowRunUrl",
        "sourceRepository",
        "reviewer",
        "observedAt",
    ):
        value = payload.get(field)
        invalid_environment = (
            field == "stagingEnvironment"
            and isinstance(value, str)
            and ("production" in value.lower())
        )
        if not _meaningful_string(value) or invalid_environment:
            errors.append(f"platform evidence requires a non-production {field}")
    workflow_url = payload.get("workflowRunUrl")
    if _meaningful_string(workflow_url) and not str(workflow_url).startswith("https://"):
        errors.append("workflowRunUrl must use HTTPS")
    try:
        observed = _parse_utc(payload.get("observedAt"))
        age = dt.datetime.now(dt.timezone.utc) - observed
        if age < dt.timedelta(0) or age > dt.timedelta(days=14):
            errors.append("platform evidence must be no older than 14 days")
    except ValueError:
        errors.append("observedAt must be ISO-8601")
    security = _passed_case_ids(payload.get("securityCases"), "securityCases", errors)
    recovery = _passed_case_ids(payload.get("recoveryCases"), "recoveryCases", errors)
    for missing in sorted(REQUIRED_SECURITY_CASES - security):
        errors.append(f"missing passing security case: {missing}")
    for missing in sorted(REQUIRED_RECOVERY_CASES - recovery):
        errors.append(f"missing passing recovery case: {missing}")
    if payload.get("containsProductionSecrets") is not False:
        errors.append("platform evidence must confirm production-secret isolation")
    if errors:
        sys.stderr.write("\n".join(errors) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
