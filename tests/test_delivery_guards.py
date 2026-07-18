from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

import pytest

from botparty_robot.faults import FaultRegistry
from botparty_robot.redaction import SECRET_PLACEHOLDER, redact_structure, redact_text

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str) -> ModuleType:
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_evidence_checker(
    tmp_path: Path, script: str, payload: object, *arguments: str
) -> subprocess.CompletedProcess[str]:
    evidence = tmp_path / f"{script}.json"
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), str(evidence), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def _iso(delta: dt.timedelta) -> str:
    return (dt.datetime.now(dt.timezone.utc) + delta).isoformat().replace("+00:00", "Z")


def test_legacy_removal_deadline_is_enforced() -> None:
    before = subprocess.run(
        [sys.executable, "scripts/check-legacy-deadline.py", "--as-of", "2026-08-31"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    at_deadline = subprocess.run(
        [sys.executable, "scripts/check-legacy-deadline.py", "--as-of", "2026-09-01"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert before.returncode == 0
    assert at_deadline.returncode != 0
    assert "legacy config removal deadline" in at_deadline.stderr


def test_canary_promotion_report_is_release_bound_and_metric_gated(tmp_path: Path) -> None:
    commit = "a" * 40
    report = {
        "schemaVersion": 1,
        "releaseTag": "v0.2.0",
        "commit": commit,
        "buildId": f"sha256-{'b' * 64}",
        "observedAt": _iso(dt.timedelta(minutes=-5)),
        "reviewer": "release-reviewer-17",
        "devices": [
            {
                "deviceEvidenceId": f"canary-device-{index}",
                "controlReady": True,
                "mediaReady": True,
                "safeStopConfirmed": True,
                "powerLossRecovered": True,
                "rollbackConfirmed": True,
                "soakHours": 2.5,
                "metrics": {
                    "commandSuccessRate": 0.999,
                    "mediaAvailability": 0.995,
                    "stopP99Ms": 80,
                    "reconnectRecoveryP99Sec": 8,
                },
            }
            for index in range(2)
        ],
    }
    path = tmp_path / "canary.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    command = [
        sys.executable,
        "scripts/check-canary-report.py",
        str(path),
        "--tag",
        "v0.2.0",
        "--commit",
        commit,
    ]

    accepted = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    report["devices"][0]["metrics"]["stopP99Ms"] = 151
    path.write_text(json.dumps(report), encoding="utf-8")
    rejected = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)

    assert accepted.returncode == 0
    assert rejected.returncode != 0
    assert "stopP99Ms" in rejected.stderr


def test_readme_backup_key_command_is_executable(tmp_path) -> None:
    key = tmp_path / "backup.key"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "botparty_robot",
            "backup",
            "generate-key",
            "--key-file",
            str(key),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == str(key)
    assert key.stat().st_mode & 0o777 == 0o600


def test_installer_dry_run_validates_plan_without_writes(tmp_path) -> None:
    prefix = tmp_path / "prefix"
    config = tmp_path / "config" / "config.yaml"
    state = tmp_path / "state"
    result = subprocess.run(
        [
            "bash",
            "scripts/install-botparty-client.sh",
            "--dry-run",
            "--no-service",
            "--no-apt",
            "--no-streamer",
            "--prefix",
            str(prefix),
            "--config",
            str(config),
            "--state-dir",
            str(state),
            "--extras",
            "mqtt,serial",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Profiles: mqtt serial" in result.stdout
    assert not prefix.exists()
    assert not state.exists()


def test_offline_commission_report_has_stable_non_moving_phases(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    report = tmp_path / "commission.json"
    config.write_text(
        "server:\n"
        "  claim_token: test-claim-token\n"
        "hardware:\n"
        "  type: none\n"
        "video:\n"
        "  type: none\n"
        "tts:\n"
        "  enabled: false\n"
        "  type: none\n",
        encoding="utf-8",
    )
    config.chmod(0o600)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "botparty_robot",
            "--config",
            str(config),
            "commission",
            "--output",
            str(report),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    phases = {phase["id"]: phase for phase in payload["phases"]}

    assert result.returncode == 0
    expected = {
        "host",
        "service",
        "data_processing",
        "motion_guard",
        "claim",
        "control",
        "media",
    }
    assert expected <= set(phases)
    assert phases["motion_guard"]["status"] == "passed"
    assert phases["claim"]["status"] == "skipped"


@pytest.mark.parametrize("unsafe", ["/", "/etc", "relative/path"])
def test_installer_dry_run_rejects_unsafe_prefix(tmp_path, unsafe: str) -> None:
    result = subprocess.run(
        [
            "bash",
            "scripts/install-botparty-client.sh",
            "--dry-run",
            "--prefix",
            unsafe,
            "--config",
            str(tmp_path / "config" / "config.yaml"),
            "--state-dir",
            str(tmp_path / "state"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


def test_repository_policy_validator_detects_bypass_and_missing_checks() -> None:
    module = _load_script("check-repository-policy.py")
    policy = json.loads((ROOT / ".github/repository-policy.json").read_text(encoding="utf-8"))
    actual = {
        "required_status_checks": {"contexts": policy["requiredChecks"]},
        "required_pull_request_reviews": {"required_approving_review_count": 1},
        "required_conversation_resolution": {"enabled": True},
        "required_signatures": {"enabled": True},
        "enforce_admins": {"enabled": True},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
    }
    assert module.validate(policy, actual) == []
    actual["enforce_admins"] = {"enabled": False}
    actual["required_status_checks"] = {"contexts": []}
    failures = module.validate(policy, actual)
    assert any("missing required checks" in failure for failure in failures)
    assert "administrators can bypass protection" in failures


def test_privacy_corpus_is_redacted_in_text_and_nested_structures() -> None:
    corpus = [
        "AK" + "IA1234567890ABCDEF",
        "gh" + "p_abcdefghijklmnopqrstuvwxyz1234567890",
        "xox" + "b-1234567890-abcdefghijklmnop",
        "ey" + "Jabcdefgh.ijklmnop.qrstuvwx",
    ]
    for secret in corpus:
        assert secret not in redact_text(f"credential={secret} payload={secret}")
    nested = {
        "server": {"claim-token": corpus[3]},
        "items": [{"clientSecret": corpus[1]}],
        "url": "https://robot:credential@example.com/path",
        "safe": "camera-front",
    }
    redacted = redact_structure(nested)
    encoded = json.dumps(redacted)
    assert all(secret not in encoded for secret in corpus)
    assert redacted["server"]["claim-token"] == SECRET_PLACEHOLDER
    assert redacted["safe"] == "camera-front"
    assert "robot:credential" not in redacted["url"]


def test_fault_registry_is_bounded_stable_and_redacted(monkeypatch) -> None:
    monkeypatch.setattr("botparty_robot.faults.time.time", lambda: 1234.9)
    registry = FaultRegistry(capacity=2)
    registry.record("camera_failed", "media", retryable=True, safe_detail="token=secret-value")
    registry.record("gateway_failed", "control", retryable=True)
    registry.record("stop_failed", "safety", retryable=False)
    snapshot = registry.snapshot()
    assert [fault["code"] for fault in snapshot] == ["gateway_failed", "stop_failed"]
    assert all(fault["occurred_at"] == 1234 for fault in snapshot)
    with pytest.raises(ValueError):
        registry.record("Not Stable", "media", retryable=False)


def test_secret_scanner_positive_fixtures_and_allow_marker() -> None:
    module = _load_script("check-secrets.py")
    provider_fixture = "claim_token=" + "abcdefghijklmnopqrstuvwxyz1234"
    assert module.scan_text(provider_fixture, "fixture") == ["fixture:1: provider API key"]
    assert not module.scan_text(
        "claim_token=abcdefghijklmnopqrstuvwxyz1234 # secret-scan: allow-test-fixture", "fixture"
    )
    assert module.scan_text(
        "value='aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vW1xY'",  # secret-scan: allow-test-fixture
        "fixture",
    ) == ["fixture:1: generic high-entropy value"]


def test_document_checker_rejects_missing_anchor_and_external_404(tmp_path) -> None:
    module = _load_script("check-doc-links.py")
    target = tmp_path / "target.md"
    source = tmp_path / "source.md"
    target.write_text("# Present\n", encoding="utf-8")
    source.write_text("[missing](target.md#absent)\n", encoding="utf-8")
    assert module.main([str(source)]) == 1

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:
            self.send_response(404)
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        error = module.check_external(f"http://127.0.0.1:{server.server_port}/missing", timeout=1.0)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert error == "external HTTP 404"


def test_operator_help_and_doctor_support_german_without_changing_json_contract() -> None:
    environment = dict(os.environ)
    environment["BOTPARTY_CLAIM_TOKEN"] = "locale-test-placeholder"
    help_result = subprocess.run(
        [sys.executable, "-m", "botparty_robot", "--locale", "de", "--help"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert "Konfiguration prüfen oder übertragen" in help_result.stdout

    outputs: list[object] = []
    for locale in ("en", "de"):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "botparty_robot",
                "--locale",
                locale,
                "--config",
                "config.example.yaml",
                "doctor",
                "--json",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env=environment,
        )
        assert result.returncode in {0, 1}
        outputs.append(json.loads(result.stdout))
    assert outputs[0] == outputs[1]


def test_operations_evidence_requires_traceable_fresh_drill_results(tmp_path: Path) -> None:
    payload = {
        "schemaVersion": 1,
        "deploymentId": "production-eu",
        "reviewer": "operations-reviewer",
        "observedAt": _iso(dt.timedelta(days=-1)),
        "alertRoute": "on-call-eu",
        "testAlertId": "alert-test-42",
        "testAlertDelivered": True,
        "rpoHours": 24,
        "verifiedBackupAgeHours": 2,
        "backupId": "backup-20260717",
        "offsiteCopyVerified": True,
        "rtoMinutes": 120,
        "restoreDurationMinutes": 35,
        "restoreDrillId": "restore-drill-42",
        "restoreVerified": True,
    }
    result = _run_evidence_checker(tmp_path, "check-operations-evidence.py", payload)
    assert result.returncode == 0, result.stderr

    payload["reviewer"] = "REPLACE_WITH_REVIEWER"
    payload["verifiedBackupAgeHours"] = float("nan")
    result = _run_evidence_checker(tmp_path, "check-operations-evidence.py", payload)
    assert result.returncode == 1
    assert "requires reviewer" in result.stderr
    assert "backup age exceeds RPO" in result.stderr


def test_compliance_evidence_rejects_placeholders_duplicates_and_untraced_tests(
    tmp_path: Path,
) -> None:
    deployment = {
        "deploymentId": "production-eu",
        "controller": "BotParty GmbH",
        "region": "EU",
        "legalBasis": "reviewed-policy-42",
        "retentionDays": 30,
        "processors": ["tts-provider-eu"],
        "deletionProcedure": "privacy-runbook-12",
        "exportProcedure": "privacy-runbook-13",
        "testRunId": "privacy-e2e-42",
        "consentTests": {"optOut": True, "export": True, "deletion": True},
    }
    payload = {
        "schemaVersion": 1,
        "policyVersion": "privacy-v3",
        "approvedBy": "data-protection-reviewer",
        "approvedAt": _iso(dt.timedelta(days=-2)),
        "expiresAt": _iso(dt.timedelta(days=30)),
        "deployments": [deployment],
    }
    result = _run_evidence_checker(tmp_path, "check-compliance-evidence.py", payload)
    assert result.returncode == 0, result.stderr

    payload["deployments"] = [deployment, dict(deployment, controller="REPLACE_ME")]
    result = _run_evidence_checker(tmp_path, "check-compliance-evidence.py", payload)
    assert result.returncode == 1
    assert "requires controller" in result.stderr
    assert "duplicates deploymentId" in result.stderr


def test_platform_evidence_binds_contract_commit_and_unique_test_cases(tmp_path: Path) -> None:
    contract = tmp_path / "contract.json"
    contract.write_text('{"contract":"v1"}\n', encoding="utf-8")
    commit = "a" * 40
    security_ids = {
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
    recovery_ids = {
        "claim_and_control_ready",
        "track_publish_and_first_frame",
        "outbox_reconnect_delivery",
        "emergency_stop_during_disconnect",
        "action_result_idempotency",
        "token_revocation_disconnects",
        "network_flap_recovers",
        "ota_result_after_restart",
    }

    def cases(prefix: str, ids: set[str]) -> list[dict[str, object]]:
        return [
            {"id": case_id, "passed": True, "correlationId": f"{prefix}-{index}"}
            for index, case_id in enumerate(sorted(ids))
        ]

    payload = {
        "schemaVersion": 1,
        "contractSha256": hashlib.sha256(contract.read_bytes()).hexdigest(),
        "clientVersion": "0.2.0",
        "clientCommit": commit,
        "backendVersion": "backend-sha256-42",
        "gatewayVersion": "gateway-sha256-42",
        "livekitVersion": "1.9.0",
        "stagingEnvironment": "isolated-staging-eu",
        "testRunId": "platform-e2e-42",
        "workflowRunUrl": "https://ci.staging.invalid/runs/42",
        "sourceRepository": "platform/backend",
        "reviewer": "security-reviewer",
        "observedAt": _iso(dt.timedelta(days=-1)),
        "containsProductionSecrets": False,
        "securityCases": cases("security", security_ids),
        "recoveryCases": cases("recovery", recovery_ids),
    }
    arguments = (
        "--contract",
        str(contract),
        "--client-version",
        "0.2.0",
        "--commit",
        commit,
    )
    result = _run_evidence_checker(tmp_path, "check-platform-evidence.py", payload, *arguments)
    assert result.returncode == 0, result.stderr

    payload["securityCases"].append(dict(payload["securityCases"][0]))
    payload["workflowRunUrl"] = "http://ci.staging.invalid/runs/42"
    result = _run_evidence_checker(tmp_path, "check-platform-evidence.py", payload, *arguments)
    assert result.returncode == 1
    assert "duplicates case id" in result.stderr
    assert "reuses correlationId" in result.stderr
    assert "must use HTTPS" in result.stderr


def test_soak_checker_recomputes_resource_drift_from_bound_raw_data(tmp_path: Path) -> None:
    raw = tmp_path / "soak.jsonl"
    samples = [
        {
            "recordedAtMs": 1_700_000_000_000 + index * 300_000,
            "elapsedSeconds": index * 300,
            "rssMiB": 100.0,
            "fds": 12,
            "threads": 8,
            "temperatureC": 60.0,
            "health": {
                "status": "ready",
                "ready": True,
                "gatewayConnected": True,
                "cameraFrames": index * 9_000,
            },
        }
        for index in range(288)
    ]
    raw.write_text(
        "".join(json.dumps(sample, separators=(",", ":")) + "\n" for sample in samples),
        encoding="utf-8",
    )
    report = {
        "schemaVersion": 1,
        "buildId": "sha256-abc123",
        "deviceClass": "medium",
        "durationHours": 24,
        "sampleCount": len(samples),
        "rawDataFile": raw.name,
        "rawDataSha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
        "rssSlopeMiBPerHour": 0.0,
        "fdSlopePerHour": 0.0,
        "threadSlopePerHour": 0.0,
        "maxRssMiB": 100.0,
        "maxTemperatureC": 60.0,
        "healthErrorSamples": 0,
        "averageCameraFps": 30.0,
        "cameraProgressRatio": 1.0,
    }
    result = _run_evidence_checker(tmp_path, "check-soak-report.py", report, "--raw", str(raw))
    assert result.returncode == 0, result.stderr

    report["rssSlopeMiBPerHour"] = 0.5
    report["cameraProgressRatio"] = 0.2
    result = _run_evidence_checker(tmp_path, "check-soak-report.py", report, "--raw", str(raw))
    assert result.returncode == 1
    assert "does not match raw data" in result.stderr
    assert "camera progress ratio" in result.stderr


def test_diff_coverage_parser_tracks_only_added_python_line_ranges() -> None:
    module = _load_script("check-diff-coverage.py")
    diff = """diff --git a/botparty_robot/a.py b/botparty_robot/a.py
--- a/botparty_robot/a.py
+++ b/botparty_robot/a.py
@@ -10,2 +10,4 @@
+one
+two
 context
diff --git a/botparty_robot/b.py b/botparty_robot/b.py
--- a/botparty_robot/b.py
+++ b/botparty_robot/b.py
@@ -0,0 +1,2 @@
+new
+file
"""

    assert module.parse_added_lines(diff) == {
        "botparty_robot/a.py": {10, 11, 12, 13},
        "botparty_robot/b.py": {1, 2},
    }


def test_secret_scanner_ignores_only_obvious_low_entropy_fixtures() -> None:
    module = _load_script("check-secrets.py")
    assert module.scan_text('claim_token: "fixture_token_words_only"', "fixture") == []
    allowed_fixture = "aws_access_key_id = " + "AK" + "IAAAAAAAAAAAAAAAAA"
    assert module.scan_text(allowed_fixture, "fixture") == []
    findings = module.scan_text(
        'claim_token: "aB9dE2gH4jK6mN8pQ1sT"',  # secret-scan: allow-test-fixture
        "config",
    )
    assert findings == ["config:1: provider API key"]


def test_hil_checker_rejects_placeholder_and_non_finite_evidence(tmp_path: Path) -> None:
    module = _load_script("check-hil-reports.py")
    module.ROOT = tmp_path
    module.INVENTORY_PATH = tmp_path / "docs/generated/adapter-inventory.json"
    module.INVENTORY_PATH.parent.mkdir(parents=True)
    module.INVENTORY_PATH.write_text(
        json.dumps({"schemaVersion": 3, "adapters": [{"adapterId": "test-adapter"}]}),
        encoding="utf-8",
    )
    raw = tmp_path / "reports/hil/raw/run.jsonl"
    raw.parent.mkdir(parents=True)
    raw.write_text('{"sample":1}\n', encoding="utf-8")
    report = {
        "schemaVersion": 2,
        "clientVersion": "0.2.0",
        "commit": "a" * 40,
        "rawDataSha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
        "rawDataFile": "raw/run.jsonl",
        "recordedAt": _iso(dt.timedelta(days=-1)),
        "runnerId": "hil-runner-1",
        "deviceSerialHash": "b" * 64,
        "firmwareVersion": "firmware-1",
        "powerSupply": "bench-supply-12v",
        "attestation": {
            "issuer": "https://token.actions.githubusercontent.com",
            "workflow": "hil.yml",
            "runId": "run-42",
            "reviewer": "hardware-reviewer",
        },
        "deviceClass": "medium",
        "adapterId": "test-adapter",
        "hardware": {
            "model": "pi-4",
            "os": "debian-13",
            "kernel": "6.12.0",
            "python": "3.12.3",
            "camera": "camera-v2",
            "encoder": "h264-v4l2",
        },
        "measurements": {
            "controlReadyP95Ms": 1000,
            "commandP99Ms": 50,
            "emergencyStopP99Ms": 50,
            "rssSteadyMiB": 200,
            "shutdownP99Ms": 1000,
            "soakHours": 24,
            "rssSlopeMiBPerHour": 0.1,
        },
    }
    path = tmp_path / "reports/hil/0.2.0/device.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(report), encoding="utf-8")
    assert module.validate(path) == []

    report["runnerId"] = "REPLACE_WITH_RUNNER"
    report["measurements"]["commandP99Ms"] = float("nan")
    path.write_text(json.dumps(report), encoding="utf-8")
    errors = module.validate(path)
    assert any("must not be a placeholder" in error for error in errors)
    assert any("commandP99Ms must be" in error for error in errors)
