"""Command-line entry point for the BotParty robot client."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
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
from .device_state import DeviceStateError, load_or_create_device_key, resolve_state_directory
from .operator import (
    apply_imported_config,
    create_setup_config,
    export_config,
    import_config_preview,
    run_doctor,
    write_support_bundle,
)


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
    tts["volume"] = int(tts.get("volume", legacy_volume))
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
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigLoadError(
            f"{config_path} does not exist; copy config.example.yaml or run 'botparty-robot setup'"
        ) from exc
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigLoadError(f"cannot read YAML configuration: {config_path}") from exc
    if not isinstance(raw, dict):
        raise ConfigLoadError("configuration must contain a YAML object at the top level")

    raw = _migrate_raw_config(raw)
    server = raw.get("server")
    if isinstance(server, dict):
        claim_override = os.environ.get("BOTPARTY_CLAIM_TOKEN", "").strip()
        if claim_override:
            server["claim_token"] = claim_override
    try:
        config = RobotConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigLoadError(_format_validation_error(exc)) from exc
    config._source_path = config_path

    if persist_device_key and config.server.device_key is None:
        try:
            key, _path = load_or_create_device_key(config.state)
        except DeviceStateError as exc:
            raise ConfigLoadError(str(exc)) from exc
        config.server.device_key = SecretStr(key)
    return config


def load_config() -> RobotConfig:
    return _load_config_from(None)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BotParty Robot Client")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Config YAML path (default: BOTPARTY_CONFIG or ./config.yaml)",
    )
    commands = parser.add_subparsers(dest="command")

    config_parser = commands.add_parser("config", help="Validate or transfer configuration")
    config_commands = config_parser.add_subparsers(dest="config_command", required=True)
    config_commands.add_parser("validate", help="Validate without network or hardware access")
    export_parser = config_commands.add_parser("export", help="Write a redacted portable config")
    export_parser.add_argument("--output", type=Path, required=True)
    import_parser = config_commands.add_parser("import", help="Preview or apply a config import")
    import_parser.add_argument("--input", type=Path, required=True)
    import_parser.add_argument("--apply", action="store_true")

    doctor_parser = commands.add_parser("doctor", help="Run non-moving host checks")
    doctor_parser.add_argument("--network", action="store_true", help="Also check DNS and TLS")
    doctor_parser.add_argument("--json", action="store_true")

    support_parser = commands.add_parser("support-bundle", help="Create a secret-free support ZIP")
    support_parser.add_argument("--output", type=Path, required=True)

    setup_parser = commands.add_parser("setup", help="Create a minimal config interactively")
    setup_parser.add_argument("--output", type=Path)

    backup_parser = commands.add_parser("backup", help="Create or restore encrypted device state")
    backup_commands = backup_parser.add_subparsers(dest="backup_command", required=True)
    key_parser = backup_commands.add_parser("generate-key", help="Create an operator backup key")
    key_parser.add_argument("--key-file", type=Path, required=True)
    create_parser = backup_commands.add_parser("create", help="Create an encrypted backup")
    create_parser.add_argument("--key-file", type=Path, required=True)
    create_parser.add_argument("--output", type=Path, required=True)
    restore_parser = backup_commands.add_parser("restore", help="Restore an encrypted backup")
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
    return 0


def _command_config(args: argparse.Namespace) -> int:
    if args.config_command == "import":
        raw, diff = import_config_preview(args.input, _resolve_config_path(args.config))
        print(diff or "No changes.")
        if args.apply and diff:
            apply_imported_config(raw, _resolve_config_path(args.config))
        return 0
    config = _load_config_from(args.config, persist_device_key=False)
    if args.config_command == "validate":
        print("Configuration is valid.")
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


async def async_main(args: argparse.Namespace) -> int:
    if args.command is None:
        return await _run_client(_load_config_from(args.config))
    if args.command == "config":
        return _command_config(args)
    if args.command == "setup":
        output = args.output or _resolve_config_path(args.config)
        create_setup_config(output)
        print(output)
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
                print(f"{check.status:5} {check.name}: {check.detail}")
                if check.fix:
                    print(f"      {check.fix}")
        return 1 if any(check.status == "ERROR" for check in checks) else 0
    if args.command == "support-bundle":
        write_support_bundle(
            _load_config_from(args.config, persist_device_key=False),
            args.output,
        )
        print(args.output)
        return 0
    if args.command == "backup":
        return _command_backup(args)
    raise RuntimeError(f"unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
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
