"""Command-line entry point for the BotParty robot client."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml
from pydantic import SecretStr, ValidationError

from . import __build_id__, __version__
from .backup import (
    BackupError,
    create_encrypted_backup,
    generate_backup_key,
    read_encrypted_backup,
    restore_encrypted_backup,
)
from .client import BotPartyClient, should_emit_runtime_log
from .config import RobotConfig, normalize_cameras
from .device_state import (
    DeviceStateError,
    load_or_create_device_key,
    read_configuration_file,
    resolve_state_directory,
)
from .operator import (
    apply_imported_config,
    create_setup_config,
    export_config,
    import_config_preview,
    run_doctor,
    systemd_device_allow_rules,
    write_config_schema,
    write_support_bundle,
)
from .process_group import run_sandboxed


class ConfigLoadError(RuntimeError):
    pass


def _resolve_log_level() -> int:
    configured = os.environ.get("BOTPARTY_LOG_LEVEL", "INFO").strip().upper()
    return getattr(logging, configured, logging.INFO)


logging.basicConfig(
    level=_resolve_log_level(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("botparty")
SHUTDOWN_TIMEOUT_SECONDS = 15.0
LEGACY_CONFIG_DEPRECATION_DEADLINE = "2026-09-01"

_DE_MESSAGES = {
    "description": "BotParty-Roboterclient",
    "config_path": "Pfad zur YAML-Konfiguration (Standard: BOTPARTY_CONFIG oder ./config.yaml)",
    "config": "Konfiguration prüfen oder übertragen",
    "validate": "Ohne Netzwerk- oder Hardwarezugriff prüfen",
    "export": "Bereinigte, portable Konfiguration schreiben",
    "import": "Konfigurationsimport anzeigen oder anwenden",
    "schema": "Versioniertes JSON-Schema schreiben",
    "doctor": "Nichtbewegende Host-Prüfungen ausführen",
    "commission": "Geführte nichtbewegende Freigabeprüfung ausführen",
    "network": "Zusätzlich DNS und TLS prüfen",
    "device_policy": "systemd-DeviceAllow-Regeln der Konfiguration ausgeben",
    "support": "Bereinigtes Support-ZIP erstellen",
    "setup": "Minimale Konfiguration interaktiv erstellen",
    "answers": "Setup-Antworten aus einem JSON-Objekt lesen",
    "completion": "Shell-Vervollständigung ausgeben",
    "backup": "Verschlüsselten Gerätestand sichern oder wiederherstellen",
    "generate_key": "Backup-Schlüssel für Betreiber erstellen",
    "backup_create": "Verschlüsseltes Backup erstellen",
    "backup_restore": "Verschlüsseltes Backup wiederherstellen",
    "valid": "Konfiguration ist gültig.",
    "no_changes": "Keine Änderungen.",
}


def _human(locale: str, key: str, english: str) -> str:
    return _DE_MESSAGES.get(key, english) if locale == "de" else english


def _selected_locale(argv: list[str] | None) -> str:
    arguments = list(argv if argv is not None else sys.argv[1:])
    configured = os.getenv("BOTPARTY_LOCALE", "en").strip().lower()
    for index, argument in enumerate(arguments):
        if argument.startswith("--locale="):
            configured = argument.partition("=")[2].strip().lower()
        elif argument == "--locale" and index + 1 < len(arguments):
            configured = arguments[index + 1].strip().lower()
    return configured if configured in {"en", "de"} else "en"


class PlannedReconnectNoiseFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return should_emit_runtime_log(record)


for handler in logging.getLogger().handlers:
    handler.addFilter(PlannedReconnectNoiseFilter())


def _warn_legacy_config(section: str, target: str) -> None:
    logger.warning(
        "Deprecated config section '%s'; migrate to '%s' before %s",
        section,
        target,
        LEGACY_CONFIG_DEPRECATION_DEADLINE,
    )


def _apply_legacy_hardware_defaults(raw: dict[str, Any]) -> dict[str, Any]:
    controls = raw.pop("controls", None)
    if "hardware" in raw:
        if controls is not None:
            _warn_legacy_config("controls", "hardware")
        return raw
    controls = controls if isinstance(controls, dict) else {}
    if controls.get("gpio_enabled"):
        _warn_legacy_config("controls", "hardware")
        raw["hardware"] = {
            "type": "l298n",
            "options": {
                "forward_pins": [controls.get("motor_left_forward")],
                "backward_pins": [controls.get("motor_left_backward")],
                "left_pins": [controls.get("motor_right_forward")],
                "right_pins": [controls.get("motor_right_backward")],
            },
        }
    else:
        raw["hardware"] = {"type": "none", "options": {}}
    return raw


def _apply_legacy_video_defaults(raw: dict[str, Any]) -> dict[str, Any]:
    camera = raw.get("camera")
    pipeline_raw = camera.pop("pipeline", None) if isinstance(camera, dict) else None
    if "video" in raw:
        if pipeline_raw is not None:
            _warn_legacy_config("camera.pipeline", "video.type")
        return raw
    if isinstance(pipeline_raw, str) and pipeline_raw.strip():
        _warn_legacy_config("camera.pipeline", "video.type")
        pipeline = pipeline_raw.strip().lower()
        mapping = {
            "opencv": "opencv",
            "ffmpeg": "ffmpeg",
            "libcamera": "ffmpeg_libcamera",
            "ffmpeg-libcamera": "ffmpeg_libcamera",
        }
        raw["video"] = {"type": mapping.get(pipeline, pipeline), "options": {}}
    else:
        raw["video"] = {"type": "ffmpeg", "options": {}}
    return raw


def _apply_legacy_tts_defaults(raw: dict[str, Any]) -> dict[str, Any]:
    tts = raw.get("tts")
    if not isinstance(tts, dict):
        raw["tts"] = {"enabled": False, "type": "none"}
        return raw

    speaker_device = tts.get("playback_device") or tts.pop("speaker_device", None)
    speaker_device = speaker_device or tts.pop("audio_device", None)
    speaker_num = tts.pop("speaker_num", None) or tts.pop("hw_num", None)
    if not speaker_device and isinstance(speaker_num, str) and speaker_num.strip():
        speaker_device = f"plughw:{speaker_num.strip()}"

    delay_ms = tts.get("delay_ms", 0)
    delay_tts = tts.pop("delay_tts", None)
    delay_value = tts.pop("delay", None)
    if not delay_ms and delay_tts and isinstance(delay_value, (int, float)):
        delay_ms = int(delay_value * 1_000)

    legacy_volume = tts.pop("tts_volume", 70)
    legacy_filter = tts.pop("filter_url_tts", True)
    legacy_anonymous = tts.pop("anon_tts", False)
    tts["enabled"] = bool(tts.get("enabled", tts.get("type", "none") != "none"))
    tts["type"] = str(tts.get("type", "none"))
    tts["playback_device"] = str(speaker_device or "default")
    volume_value = tts.get("volume", legacy_volume)
    try:
        tts["volume"] = int(volume_value) if volume_value is not None else 70
    except (TypeError, ValueError):
        tts["volume"] = 70
    tts["filter_urls"] = bool(tts.get("filter_urls", legacy_filter))
    tts["allow_anonymous"] = bool(tts.get("allow_anonymous", legacy_anonymous))
    tts["blocked_senders"] = list(tts.get("blocked_senders", []))
    tts["delay_ms"] = int(delay_ms or 0)
    tts["options"] = dict(tts.get("options", {}))
    raw["tts"] = tts
    return raw


def _migrate_raw_config(raw: dict[str, Any]) -> dict[str, Any]:
    raw = _apply_legacy_hardware_defaults(raw)
    raw = _apply_legacy_video_defaults(raw)
    raw = _apply_legacy_tts_defaults(raw)
    safety = raw.get("safety")
    if isinstance(safety, dict) and "emergency_stop_pin" in safety:
        safety.pop("emergency_stop_pin")
        _warn_legacy_config("safety.emergency_stop_pin", "external hardware stop circuit")
    return raw


def _uses_legacy_config(raw: dict[str, Any]) -> bool:
    controls = raw.get("controls")
    camera = raw.get("camera")
    tts = raw.get("tts")
    safety = raw.get("safety")
    return bool(
        isinstance(controls, dict)
        or (isinstance(camera, dict) and "pipeline" in camera)
        or (
            isinstance(tts, dict)
            and any(
                name in tts
                for name in (
                    "speaker_device",
                    "audio_device",
                    "speaker_num",
                    "hw_num",
                    "delay_tts",
                    "delay",
                    "tts_volume",
                    "filter_url_tts",
                    "anon_tts",
                )
            )
        )
        or (isinstance(safety, dict) and "emergency_stop_pin" in safety)
    )


def _format_validation_error(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors(include_input=False, include_url=False):
        path = ".".join(str(part) for part in error["loc"]) or "config"
        lines.append(f"{path}: {error['msg']}")
    return "\n".join(lines)


def _resolve_config_path(path_override: str | None) -> Path:
    env_path = os.environ.get("BOTPARTY_CONFIG")
    return Path(path_override or env_path or "config.yaml").expanduser().resolve()


def _load_config_from(
    path_override: str | None,
    *,
    persist_device_key: bool = True,
) -> RobotConfig:
    config_path = _resolve_config_path(path_override)
    try:
        raw = yaml.safe_load(
            read_configuration_file(
                config_path,
                allow_public_example=config_path.name == "config.example.yaml",
            ).decode("utf-8")
        )
    except FileNotFoundError as exc:
        raise ConfigLoadError(
            f"{config_path} does not exist; copy config.example.yaml or run 'botparty-robot setup'"
        ) from exc
    except DeviceStateError as exc:
        raise ConfigLoadError(str(exc)) from exc
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigLoadError(f"cannot read YAML configuration: {config_path}") from exc
    if not isinstance(raw, dict):
        raise ConfigLoadError("configuration must contain a YAML object at the top level")

    legacy_migration_used = _uses_legacy_config(raw)
    raw = _migrate_raw_config(raw)
    server = raw.get("server")
    if isinstance(server, dict):
        claim_override = os.environ.get("BOTPARTY_CLAIM_TOKEN", "").strip()
        if claim_override:
            server["claim_token"] = claim_override

    ota_overrides = {
        "manifest_url": os.environ.get("BOTPARTY_OTA_MANIFEST_URL", "").strip(),
        "public_key_file": os.environ.get("BOTPARTY_OTA_PUBLIC_KEY_FILE", "").strip(),
        "state_directory": os.environ.get("BOTPARTY_OTA_STATE_DIR", "").strip(),
    }
    if any(ota_overrides.values()):
        ota = raw.setdefault("ota", {})
        if isinstance(ota, dict):
            ota.update({key: value for key, value in ota_overrides.items() if value})
    try:
        config = RobotConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigLoadError(_format_validation_error(exc)) from exc
    config._source_path = config_path
    config._legacy_migration_used = legacy_migration_used

    if persist_device_key and config.server.device_key is None:
        try:
            key, _path = load_or_create_device_key(config.state)
        except DeviceStateError as exc:
            raise ConfigLoadError(str(exc)) from exc
        config.server.device_key = SecretStr(key)
    return config


def load_config() -> RobotConfig:
    return _load_config_from(None)


def _build_parser(locale: str = "en") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=_human(locale, "description", "BotParty Robot Client")
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--locale",
        choices=["en", "de"],
        default=locale,
        help="Operator language / Sprache (default: BOTPARTY_LOCALE or en)",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help=_human(
            locale,
            "config_path",
            "Config YAML path (default: BOTPARTY_CONFIG or ./config.yaml)",
        ),
    )
    commands = parser.add_subparsers(dest="command")

    config_parser = commands.add_parser(
        "config", help=_human(locale, "config", "Validate or transfer configuration")
    )
    config_commands = config_parser.add_subparsers(dest="config_command", required=True)
    config_commands.add_parser(
        "validate",
        help=_human(locale, "validate", "Validate without network or hardware access"),
    )
    export_parser = config_commands.add_parser(
        "export", help=_human(locale, "export", "Write a redacted portable config")
    )
    export_parser.add_argument("--output", type=Path, required=True)
    import_parser = config_commands.add_parser(
        "import", help=_human(locale, "import", "Preview or apply a config import")
    )
    import_parser.add_argument("--input", type=Path, required=True)
    import_parser.add_argument("--apply", action="store_true")
    schema_parser = config_commands.add_parser(
        "schema", help=_human(locale, "schema", "Write the versioned JSON schema")
    )
    schema_parser.add_argument("--output", type=Path, required=True)

    doctor_parser = commands.add_parser(
        "doctor", help=_human(locale, "doctor", "Run non-moving host checks")
    )
    doctor_parser.add_argument(
        "--network",
        action="store_true",
        help=_human(locale, "network", "Also check DNS and TLS"),
    )
    doctor_parser.add_argument("--json", action="store_true")

    commission_parser = commands.add_parser(
        "commission",
        help=_human(locale, "commission", "Run a guided non-moving release check"),
    )
    commission_parser.add_argument("--online", action="store_true")
    commission_parser.add_argument("--timeout", type=float, default=45.0)
    commission_parser.add_argument("--output", type=Path)

    commands.add_parser(
        "device-policy",
        help=_human(
            locale,
            "device_policy",
            "Print systemd DeviceAllow rules for the effective configuration",
        ),
    )

    support_parser = commands.add_parser(
        "support-bundle", help=_human(locale, "support", "Create a secret-free support ZIP")
    )
    support_parser.add_argument("--output", type=Path, required=True)

    setup_parser = commands.add_parser(
        "setup", help=_human(locale, "setup", "Create a minimal config interactively")
    )
    setup_parser.add_argument("--output", type=Path)
    setup_parser.add_argument(
        "--answers",
        type=Path,
        help=_human(locale, "answers", "Read non-interactive setup answers from a JSON object"),
    )

    completion_parser = commands.add_parser(
        "completion", help=_human(locale, "completion", "Print shell completion setup")
    )
    completion_parser.add_argument("shell", choices=["bash", "zsh", "fish"])

    backup_parser = commands.add_parser(
        "backup", help=_human(locale, "backup", "Create or restore encrypted device state")
    )
    backup_commands = backup_parser.add_subparsers(dest="backup_command", required=True)
    key_parser = backup_commands.add_parser(
        "generate-key", help=_human(locale, "generate_key", "Create an operator backup key")
    )
    key_parser.add_argument("--key-file", type=Path, required=True)
    create_parser = backup_commands.add_parser(
        "create", help=_human(locale, "backup_create", "Create an encrypted backup")
    )
    create_parser.add_argument("--key-file", type=Path, required=True)
    create_parser.add_argument("--output", type=Path, required=True)
    restore_parser = backup_commands.add_parser(
        "restore", help=_human(locale, "backup_restore", "Restore an encrypted backup")
    )
    restore_parser.add_argument("--key-file", type=Path, required=True)
    restore_parser.add_argument("--input", type=Path, required=True)
    restore_parser.add_argument("--state-dir", type=Path)
    return parser


async def _shutdown_with_timeout(
    client: BotPartyClient,
    main_task: asyncio.Task[None],
) -> None:
    try:
        await asyncio.wait_for(client.shutdown(), timeout=SHUTDOWN_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.error("Shutdown exceeded %.1f seconds", SHUTDOWN_TIMEOUT_SECONDS)
        main_task.cancel()


async def _run_client(config: RobotConfig) -> int:
    if config.server.claim_token_value() == "PASTE_YOUR_CLAIM_TOKEN_HERE":
        logger.error("Set server.claim_token or BOTPARTY_CLAIM_TOKEN before starting")
        return 2
    if config.server.allow_insecure_dev_transport:
        logger.warning("Insecure loopback development transport is enabled")
    client = BotPartyClient(config)
    logger.info("BotParty Robot Client v%s (build %s)", __version__, __build_id__)
    logger.info("API: %s", config.server.api_url)
    logger.info("LiveKit: %s", config.server.livekit_url)
    logger.info("Hardware: %s", config.hardware.type)
    logger.info("Video: %s", config.video.type)
    logger.info("TTS: %s (enabled=%s)", config.tts.type, config.tts.enabled)
    if client._health_enabled():
        logger.info("Health: http://%s:%d/health", client._health_host(), client._health_port())
    for camera in normalize_cameras(config):
        logger.info(
            "Camera %s (%s): %s %dx%d@%dfps profile=%s",
            camera.label,
            camera.id,
            camera.camera.device,
            camera.camera.width,
            camera.camera.height,
            camera.camera.fps,
            camera.video.type,
        )

    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    if main_task is None:
        raise RuntimeError("main task is not available")
    shutdown_task: asyncio.Task[None] | None = None

    def request_shutdown() -> None:
        nonlocal shutdown_task
        if shutdown_task is None or shutdown_task.done():
            shutdown_task = asyncio.create_task(_shutdown_with_timeout(client, main_task))

    for caught_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(caught_signal, request_shutdown)
    try:
        await client.run()
    except asyncio.CancelledError:
        if shutdown_task is None or not shutdown_task.done():
            raise
    if shutdown_task is not None:
        await shutdown_task
    return 0


def _command_config(args: argparse.Namespace) -> int:
    if args.config_command == "import":
        raw, diff = import_config_preview(args.input, _resolve_config_path(args.config))
        print(diff or _human(args.locale, "no_changes", "No changes."))
        if args.apply and diff:
            apply_imported_config(raw, _resolve_config_path(args.config))
        return 0
    if args.config_command == "schema":
        write_config_schema(args.output)
        print(args.output)
        return 0
    config = _load_config_from(args.config, persist_device_key=False)
    if args.config_command == "validate":
        print(_human(args.locale, "valid", "Configuration is valid."))
        return 0
    if args.config_command == "export":
        export_config(config, args.output)
        print(args.output)
        return 0
    raise RuntimeError(f"unsupported config command: {args.config_command}")


def _command_backup(args: argparse.Namespace) -> int:
    if args.backup_command == "generate-key":
        generate_backup_key(args.key_file)
        print(args.key_file)
        return 0
    config_path = _resolve_config_path(args.config)
    if args.backup_command == "create":
        config = _load_config_from(args.config, persist_device_key=True)
        _key, device_key_path = load_or_create_device_key(config.state)
        create_encrypted_backup(
            config_path=config_path,
            device_key_path=device_key_path,
            output_path=args.output,
            key_path=args.key_file,
        )
        print(args.output)
        return 0

    files = read_encrypted_backup(args.input, args.key_file)
    raw = yaml.safe_load(files["config.yaml"])
    if not isinstance(raw, dict):
        raise BackupError("backup configuration is invalid")
    try:
        restored_config = RobotConfig.model_validate(_migrate_raw_config(raw))
    except ValidationError as exc:
        raise BackupError(_format_validation_error(exc)) from exc
    state_dir = args.state_dir or resolve_state_directory(restored_config.state)
    device_key_path = state_dir / restored_config.state.device_key_file
    restore_encrypted_backup(
        input_path=args.input,
        key_path=args.key_file,
        config_path=config_path,
        device_key_path=device_key_path,
    )
    print(config_path)
    return 0


async def _command_commission(args: argparse.Namespace) -> int:
    if not 5 <= args.timeout <= 300:
        raise ValueError("commission timeout must be between 5 and 300 seconds")
    config = _load_config_from(args.config, persist_device_key=args.online)
    checks = run_doctor(config, network=args.online)
    phases: list[dict[str, object]] = [
        {
            "id": "host",
            "status": "failed" if any(check.status == "ERROR" for check in checks) else "passed",
            "checks": [asdict(check) for check in checks],
        }
    ]
    unit_path = Path("/etc/systemd/system/botparty-robot.service")
    systemctl = shutil.which("systemctl")
    if unit_path.is_file() and systemctl is not None:
        enabled = run_sandboxed(
            [systemctl, "is-enabled", "botparty-robot.service"],
            timeout=5,
            capture_output=True,
            text=True,
        )
        phases.append(
            {
                "id": "service",
                "status": "passed" if enabled.returncode == 0 else "failed",
                "code": "unit_enabled" if enabled.returncode == 0 else "unit_not_enabled",
            }
        )
    else:
        phases.append({"id": "service", "status": "skipped", "code": "systemd_not_detected"})
    cloud_tts = config.tts.enabled and config.tts.type in {"google_cloud", "polly"}
    cloud_consent = bool(config.tts.options.get("cloud_data_processing_accepted", False))
    phases.append(
        {
            "id": "data_processing",
            "status": "failed" if cloud_tts and not cloud_consent else "passed",
            "code": (
                "cloud_tts_consent_missing"
                if cloud_tts and not cloud_consent
                else "configured_opt_in_state_recorded"
            ),
            "diagnosticsEnabled": config.diagnostics.upload_enabled,
            "telemetryEnabled": config.telemetry.operational_enabled,
            "productAnalyticsEnabled": config.telemetry.product_analytics_enabled,
        }
    )
    non_moving = config.hardware.type == "none"
    phases.append(
        {
            "id": "motion_guard",
            "status": "passed" if non_moving else "failed",
            "code": "hardware_none" if non_moving else "moving_hardware_configured",
        }
    )
    if args.online and non_moving:
        client = BotPartyClient(config)
        run_task = asyncio.create_task(client.run())
        deadline = asyncio.get_running_loop().time() + args.timeout
        try:
            while asyncio.get_running_loop().time() < deadline:
                if run_task.done():
                    break
                media_ok = not client._media_required() or client._total_camera_frames() > 0
                if client._gateway.connected and media_ok:
                    break
                await asyncio.sleep(0.1)
        finally:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(client.shutdown(), timeout=10)
            if not run_task.done():
                run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await run_task
        claimed = bool(client._robot_id)
        control_ready = client._gateway.connected
        media_required = client._media_required()
        media_ready = not media_required or client._total_camera_frames() > 0
        phases.extend(
            [
                {
                    "id": "claim",
                    "status": "passed" if claimed else "failed",
                    "code": "identity_claimed" if claimed else "claim_not_completed",
                },
                {
                    "id": "control",
                    "status": "passed" if control_ready else "failed",
                    "code": "gateway_ready" if control_ready else "gateway_not_ready",
                },
                {
                    "id": "media",
                    "status": "passed" if media_ready else "failed",
                    "code": (
                        "media_not_required"
                        if not media_required
                        else "first_frame_received"
                        if media_ready
                        else "first_frame_missing"
                    ),
                },
            ]
        )
    elif args.online:
        phases.extend(
            {"id": phase, "status": "skipped", "code": "motion_guard_failed"}
            for phase in ("claim", "control", "media")
        )
    else:
        phases.extend(
            {"id": phase, "status": "skipped", "code": "offline_mode"}
            for phase in ("claim", "control", "media")
        )

    passed = all(phase["status"] != "failed" for phase in phases)
    report = {
        "schemaVersion": 1,
        "clientVersion": __version__,
        "buildId": __build_id__,
        "mode": "online" if args.online else "offline",
        "passed": passed,
        "phases": phases,
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(args.output)
    else:
        print(rendered, end="")
    return 0 if passed else 1


async def async_main(args: argparse.Namespace) -> int:
    if args.command is None:
        return await _run_client(_load_config_from(args.config))
    if args.command == "config":
        return _command_config(args)
    if args.command == "setup":
        output = args.output or _resolve_config_path(args.config)
        answers = None
        if args.answers is not None:
            payload = json.loads(args.answers.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("setup answers must contain a JSON object")
            answers = payload
        create_setup_config(output, answers=answers, locale=args.locale)
        print(output)
        return 0
    if args.command == "completion":
        command_names = (
            "config doctor commission device-policy support-bundle setup backup completion"
        )
        snippets = {
            "bash": f'complete -W "{command_names}" botparty-robot',
            "zsh": f"compdef \"_arguments '1:command:({command_names})'\" botparty-robot",
            "fish": f"complete -c botparty-robot -f -a '{command_names}'",
        }
        print(snippets[args.shell])
        return 0
    if args.command == "doctor":
        checks = run_doctor(
            _load_config_from(args.config, persist_device_key=False),
            network=args.network,
        )
        if args.json:
            print(json.dumps([asdict(check) for check in checks], indent=2))
        else:
            for check in checks:
                status = (
                    "FEHLER" if args.locale == "de" and check.status == "ERROR" else check.status
                )
                label = "Prüfung" if args.locale == "de" else ""
                print(f"{status:6} {label + ' ' if label else ''}{check.name}: {check.detail}")
                if check.fix:
                    prefix = "Behebung:" if args.locale == "de" else ""
                    print(f"       {prefix + ' ' if prefix else ''}{check.fix}")
        return 1 if any(check.status == "ERROR" for check in checks) else 0
    if args.command == "commission":
        return await _command_commission(args)
    if args.command == "support-bundle":
        write_support_bundle(
            _load_config_from(args.config, persist_device_key=False),
            args.output,
        )
        print(args.output)
        return 0
    if args.command == "device-policy":
        config = _load_config_from(args.config, persist_device_key=False)
        for rule in systemd_device_allow_rules(config):
            print(rule)
        return 0
    if args.command == "backup":
        return _command_backup(args)
    raise RuntimeError(f"unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser(_selected_locale(argv))
    args = parser.parse_args(argv)
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        logger.info("Cancelled.")
        return 130
    except (ConfigLoadError, BackupError, DeviceStateError, ValueError) as exc:
        logger.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
