"""Non-moving operator diagnostics and portable configuration helpers."""

from __future__ import annotations

import difflib
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import socket
import ssl
import stat
import sys
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import yaml

from . import __version__
from .artifacts import ArtifactVerificationError, load_public_key, verify_installed_streamer
from .audio import list_alsa_devices
from .config import (
    DEFAULT_OTA_MANIFEST_URL,
    DEFAULT_OTA_PUBLIC_KEY_FILE,
    DEFAULT_OTA_STATE_DIRECTORY,
    NormalizedCameraConfig,
    RobotConfig,
    normalize_cameras,
)
from .device_state import DeviceStateError, resolve_state_directory, validate_configuration_file
from .hardware import missing_hardware_dependency, normalize_profile_name
from .profile_options import profile_options_schema
from .redaction import SECRET_PLACEHOLDER, redact_structure, redact_text
from .tts.custom import validate_custom_tts_module
from .video.base import BaseVideoProfile

MAX_SUPPORT_BUNDLE_BYTES = 2 * 1024 * 1024


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    status: str
    detail: str
    fix: str | None = None


def _private_readable_credential(path: str) -> bool:
    if not path:
        return False
    candidate = Path(path).expanduser()
    try:
        metadata = candidate.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and metadata.st_uid in {0, os.geteuid()}
        and not stat.S_IMODE(metadata.st_mode) & 0o027
        and metadata.st_size <= 64 * 1024
        and os.access(candidate, os.R_OK)
    )


def redacted_config(config: RobotConfig) -> dict[str, Any]:
    exported = redact_structure(config.model_dump(mode="json"))
    server = exported["server"]
    server["claim_token"] = "${BOTPARTY_CLAIM_TOKEN}"
    server["device_key"] = None
    server["robot_auth_token"] = None
    return {
        "schemaVersion": 1,
        "requirements": _config_requirements(config),
        "config": exported,
    }


def _config_requirements(config: RobotConfig) -> dict[str, object]:
    cameras = normalize_cameras(config)
    return {
        "hardwareProfile": config.hardware.type,
        "videoProfiles": sorted({camera.video.type for camera in cameras if camera.enabled}),
        "ttsProfile": config.tts.type if config.tts.enabled else "none",
    }


def systemd_device_allow_rules(config: RobotConfig) -> list[str]:
    """Return the minimal systemd DeviceAllow rules for the effective config."""

    devices: set[str] = set()
    classes: set[str] = set()
    cameras = [camera for camera in normalize_cameras(config) if camera.enabled]
    for camera in cameras:
        if (
            camera.video.type not in {"none", "cozmo_vid", "vector_vid"}
            and isinstance(camera.camera.device, str)
            and camera.camera.device.startswith("/dev/")
        ):
            devices.add(str(Path(camera.camera.device).resolve(strict=False)))
        if camera.video.type in {"ffmpeg_arecord", "botparty_streamer"}:
            classes.add("char-alsa")

    profile = config.hardware.type
    options = config.hardware.options
    if profile in {"serial_board", "telly"}:
        devices.add(str(Path(str(options.get("device", "/dev/ttyUSB0"))).resolve(strict=False)))
    if profile == "navq":
        address = str(options.get("system_address", "serial:///dev/ttymxc2:921600"))
        if address.startswith("serial:///"):
            devices.add(address.removeprefix("serial://").rsplit(":", 1)[0])
    if profile in {"adafruit_pwm", "motor_hat", "thunderborg"}:
        bus = int(options.get("i2c_bus", options.get("bus", 1)))
        devices.add(f"/dev/i2c-{bus}")
    if profile == "max7219":
        devices.add(f"/dev/spidev{int(options.get('bus', 0))}.{int(options.get('device', 0))}")
    if profile in {"l298n", "mdd10", "motozero"}:
        devices.add("/dev/gpiochip0")
    if profile in {"cozmo", "owi_arm", "vector"}:
        classes.add("char-usb_device")

    rules = [f"DeviceAllow={device} rw" for device in sorted(devices)]
    rules.extend(f"DeviceAllow={device_class} rw" for device_class in sorted(classes))
    return rules


def _atomic_write_text(
    target: Path,
    content: str,
    *,
    mode: int,
    backup_suffix: str | None = None,
) -> None:
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent,
            prefix=f".{target.name}.",
            mode="w",
            encoding="utf-8",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        if backup_suffix is not None and target.exists():
            shutil.copy2(target, target.with_suffix(target.suffix + backup_suffix))
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def export_config(config: RobotConfig, output: Path) -> None:
    payload = redacted_config(config)
    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)
    _atomic_write_text(output, text, mode=0o600)


def write_config_schema(output: Path) -> None:
    payload = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://botparty.live/schemas/robot-config-v1.json",
        "schemaVersion": 1,
        "robotConfig": RobotConfig.model_json_schema(),
        "profileOptions": profile_options_schema(),
    }
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    _atomic_write_text(output, content, mode=0o644)


def import_config_preview(input_path: Path, target_path: Path) -> tuple[dict[str, Any], str]:
    try:
        envelope = yaml.safe_load(input_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError("config import file is not readable YAML") from exc
    if not isinstance(envelope, dict) or envelope.get("schemaVersion") != 1:
        raise ValueError("config import schema is not supported")
    raw = envelope.get("config")
    if not isinstance(raw, dict):
        raise ValueError("config import has no config object")
    server = raw.get("server")
    if not isinstance(server, dict):
        raise ValueError("config import has no server object")
    claim_token = os.getenv("BOTPARTY_CLAIM_TOKEN", "").strip()
    if server.get("claim_token") == "${BOTPARTY_CLAIM_TOKEN}":
        if not claim_token:
            raise ValueError("BOTPARTY_CLAIM_TOKEN is required to import this redacted config")
        server["claim_token"] = claim_token
    server.pop("robot_auth_token", None)
    server.pop("device_key", None)
    imported_config = RobotConfig.model_validate(raw)
    requirements = envelope.get("requirements")
    if requirements is not None and requirements != _config_requirements(imported_config):
        raise ValueError("config import requirements do not match the effective configuration")
    dependency = missing_hardware_dependency(imported_config.hardware.type)
    if dependency is not None:
        modules, package = dependency
        raise ValueError(
            f"config import requires {' or '.join(modules)}; install {package} before import"
        )
    if (
        any(camera.video.type == "opencv" for camera in normalize_cameras(imported_config))
        and importlib.util.find_spec("cv2") is None
    ):
        raise ValueError("config import requires the vision extra for the OpenCV profile")

    proposed_for_diff = redact_structure(raw)
    proposed_for_diff["server"]["claim_token"] = "${BOTPARTY_CLAIM_TOKEN}"
    proposed = yaml.safe_dump(proposed_for_diff, sort_keys=False, allow_unicode=False)
    current_for_diff: object = {}
    if target_path.exists():
        try:
            current_for_diff = yaml.safe_load(target_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            current_for_diff = {"unreadableCurrentConfig": SECRET_PLACEHOLDER}
    current = yaml.safe_dump(
        redact_structure(current_for_diff),
        sort_keys=False,
        allow_unicode=False,
    )
    diff = "".join(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile=str(target_path),
            tofile=f"{target_path} (import)",
        )
    )
    return raw, diff


def apply_imported_config(raw: dict[str, Any], target_path: Path) -> None:
    content = yaml.safe_dump(raw, sort_keys=False, allow_unicode=False)
    _atomic_write_text(
        target_path,
        content,
        mode=0o600,
        backup_suffix=".before-import",
    )


def create_setup_config(
    output: Path,
    input_fn: Callable[[str], str] = input,
    answers: dict[str, object] | None = None,
    locale: str = "en",
) -> None:
    supplied = dict(answers or {})

    def answer(name: str, prompt: str, default: str) -> str:
        if answers is not None:
            value = supplied.pop(name, default)
            if not isinstance(value, (str, int)):
                raise ValueError(f"setup answer {name!r} must be a string")
            return str(value).strip() or default
        return input_fn(prompt).strip() or default

    prompts = {
        "video": "Videoprofil [ffmpeg]: " if locale == "de" else "Video profile [ffmpeg]: ",
        "camera": "Kameragerät" if locale == "de" else "Camera device",
        "api": "API-URL" if locale == "de" else "API URL",
        "livekit": "LiveKit-URL" if locale == "de" else "LiveKit URL",
        "claim": (
            "Claim-Token [später mit BOTPARTY_CLAIM_TOKEN setzen]: "
            if locale == "de"
            else "Claim token [set later with BOTPARTY_CLAIM_TOKEN]: "
        ),
    }
    video = answer("video_profile", prompts["video"], "ffmpeg").lower()
    if video not in {"ffmpeg", "opencv", "none"}:
        raise ValueError("guided setup supports video_profile ffmpeg, opencv or none")
    available_devices = sorted(
        {
            str(path)
            for pattern in ("/dev/v4l/by-id/*", "/dev/video*")
            for path in Path("/").glob(pattern.removeprefix("/"))
            if path.exists()
        }
    )
    default_device = available_devices[0] if available_devices else "/dev/video0"
    camera_device = answer(
        "camera_device",
        f"{prompts['camera']} [{default_device}]: ",
        default_device,
    )
    if video == "opencv" and camera_device.isdecimal():
        camera_value: str | int = int(camera_device)
    else:
        camera_value = camera_device
    api_url = answer(
        "api_url",
        f"{prompts['api']} [https://botparty.live]: ",
        "https://botparty.live",
    )
    livekit_url = answer(
        "livekit_url",
        f"{prompts['livekit']} [wss://botparty.live]: ",
        "wss://botparty.live",
    )
    claim_token = answer(
        "claim_token",
        prompts["claim"],
        "PASTE_YOUR_CLAIM_TOKEN_HERE",
    )
    if supplied:
        raise ValueError(f"unknown setup answers: {', '.join(sorted(supplied))}")
    raw = {
        "server": {
            "api_url": api_url,
            "livekit_url": livekit_url,
            "claim_token": claim_token,
        },
        "camera": {"device": camera_value},
        "hardware": {"type": "none", "options": {}},
        "video": {"type": video, "options": {}},
        "tts": {"enabled": False, "type": "none"},
        "ota": {
            "enabled": False,
            "manifest_url": DEFAULT_OTA_MANIFEST_URL,
            "public_key_file": str(DEFAULT_OTA_PUBLIC_KEY_FILE),
            "state_directory": str(DEFAULT_OTA_STATE_DIRECTORY),
        },
    }
    RobotConfig.model_validate(raw)
    if output.exists():
        shutil.copy2(output, output.with_suffix(output.suffix + ".before-setup"))
    apply_imported_config(raw, output)


def _runtime_doctor_checks() -> tuple[list[DoctorCheck], int]:
    checks: list[DoctorCheck] = []
    supported_python = sys.version_info >= (3, 10)
    checks.append(
        DoctorCheck(
            "python",
            "OK" if supported_python else "ERROR",
            platform.python_version(),
            None if supported_python else "Install Python 3.10 or newer.",
        )
    )
    checks.append(DoctorCheck("platform", "OK", f"{platform.system()} {platform.machine()}"))
    service_uid = os.geteuid()
    checks.append(
        DoctorCheck(
            "service_user",
            "ERROR" if service_uid == 0 else "OK",
            f"uid={service_uid}",
            "Run the service as the dedicated botparty account." if service_uid == 0 else None,
        )
    )
    return checks, service_uid


def _configuration_doctor_checks(config: RobotConfig) -> list[DoctorCheck]:
    config_path = config._source_path
    if config_path is None or not config_path.exists():
        return []

    config_stat = config_path.lstat()
    try:
        validate_configuration_file(config_path)
        config_secure = True
    except DeviceStateError:
        config_secure = False
    return [
        DoctorCheck(
            "config_permissions",
            "OK" if config_secure else "ERROR",
            f"{config_path} mode={stat.S_IMODE(config_stat.st_mode):04o}",
            None
            if config_secure
            else (
                "Use mode 0600 for a service-owned development config or mode 0640 "
                "for a root-owned production config with the service group."
            ),
        )
    ]


def _hardware_doctor_checks(config: RobotConfig) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    dependency = missing_hardware_dependency(config.hardware.type)
    if dependency is not None:
        modules, package = dependency
        checks.append(
            DoctorCheck(
                "hardware_dependency",
                "ERROR",
                f"missing {' or '.join(modules)}",
                f"Install {package} for hardware profile {config.hardware.type}.",
            )
        )

    try:
        module = importlib.import_module(
            f"botparty_robot.hardware.{normalize_profile_name(config.hardware.type)}"
        )
        adapter_type = module.HardwareAdapter
        motion_commands = tuple(adapter_type.motion_commands)
        support_level = str(adapter_type.support_level)
        released = not motion_commands or support_level == "supported"
        checks.append(
            DoctorCheck(
                "hardware_release_status",
                "OK" if released else "WARN",
                (
                    f"{support_level}; motionCommands={len(motion_commands)}; "
                    f"productionMotion={'enabled' if released else 'blocked'}"
                ),
                None
                if released
                else (
                    "Use this adapter only for non-moving evaluation until current HIL "
                    "evidence exists."
                ),
            )
        )
    except (AttributeError, ImportError, ModuleNotFoundError) as exc:
        checks.append(
            DoctorCheck(
                "hardware_release_status",
                "ERROR",
                type(exc).__name__,
                "Select a registered hardware adapter.",
            )
        )
    return checks


def _state_directory_doctor_check(config: RobotConfig, service_uid: int) -> DoctorCheck:
    state_dir = resolve_state_directory(config.state)
    state_parent = state_dir if state_dir.exists() else state_dir.parent
    writable = os.access(state_parent, os.W_OK)
    secure_state = True
    if state_dir.exists():
        state_stat = state_dir.lstat()
        secure_state = (
            stat.S_ISDIR(state_stat.st_mode)
            and not stat.S_ISLNK(state_stat.st_mode)
            and state_stat.st_uid == service_uid
            and not stat.S_IMODE(state_stat.st_mode) & 0o077
        )
    state_ok = writable and secure_state
    return DoctorCheck(
        "state_directory",
        "OK" if state_ok else "ERROR",
        str(state_dir),
        None
        if state_ok
        else f"Give the service user a private writable directory at {state_parent}.",
    )


def _camera_doctor_checks(
    config: RobotConfig,
    cameras: list[NormalizedCameraConfig],
) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    checked_binaries: set[str] = set()
    for camera in cameras:
        device = camera.camera.device
        if not isinstance(device, int) and camera.video.type not in {
            "cozmo_vid",
            "vector_vid",
            "none",
        }:
            exists = Path(device).exists()
            readable = exists and os.access(device, os.R_OK)
            checks.append(
                DoctorCheck(
                    f"camera:{camera.id}:device",
                    "OK" if readable else "ERROR",
                    device,
                    None
                    if readable
                    else f"Check that {device} exists and the service user can read it.",
                )
            )
        if camera.video.type.startswith("ffmpeg") or camera.video.type == "botparty_streamer":
            ffmpeg_name = str(camera.video.options.get("ffmpeg_path", "ffmpeg"))
            if ffmpeg_name not in checked_binaries:
                checked_binaries.add(ffmpeg_name)
                ffmpeg = shutil.which(ffmpeg_name)
                checks.append(
                    DoctorCheck(
                        f"camera:{camera.id}:ffmpeg",
                        "OK" if ffmpeg else "ERROR",
                        ffmpeg or "not found",
                        None
                        if ffmpeg
                        else "Install ffmpeg with the operating system package manager.",
                    )
                )
        if camera.video.type == "ffmpeg_libcamera":
            executable = str(camera.video.options.get("libcamera_path", "libcamera-vid"))
            available = shutil.which(executable)
            checks.append(
                DoctorCheck(
                    f"camera:{camera.id}:libcamera",
                    "OK" if available else "ERROR",
                    available or "not found",
                    None if available else "Install the supported libcamera applications package.",
                )
            )
        if camera.video.type == "opencv":
            try:
                opencv_available = importlib.util.find_spec("cv2") is not None
            except (ImportError, ModuleNotFoundError, ValueError):
                opencv_available = False
            checks.append(
                DoctorCheck(
                    f"camera:{camera.id}:opencv",
                    "OK" if opencv_available else "ERROR",
                    "available" if opencv_available else "not installed",
                    None if opencv_available else "Install botparty-robot[vision].",
                )
            )
        if camera.video.type == "botparty_streamer":
            scoped = config.model_copy(deep=True)
            scoped.camera = camera.camera
            scoped.video = camera.video
            base = BaseVideoProfile(scoped)
            configured = (
                camera.video.options.get("publisher_binary")
                or camera.video.options.get("botparty_streamer_path")
                or camera.video.options.get("lk_h264_publisher_path")
            )
            binary = (
                Path(str(configured)).expanduser()
                if configured
                else base.managed_streamer_binary_path()
            )
            expected = camera.video.options.get("publisher_sha256")
            try:
                digest = verify_installed_streamer(
                    binary,
                    str(expected) if isinstance(expected, str) else None,
                )
                checks.append(
                    DoctorCheck(f"camera:{camera.id}:streamer", "OK", f"{binary} sha256={digest}")
                )
            except ArtifactVerificationError as exc:
                checks.append(
                    DoctorCheck(
                        f"camera:{camera.id}:streamer",
                        "ERROR",
                        type(exc).__name__,
                        "Install a signed botparty-streamer artifact for this platform.",
                    )
                )
    return checks


def _audio_doctor_checks(cameras: list[NormalizedCameraConfig]) -> list[DoctorCheck]:
    if any(camera.video.type in {"ffmpeg_arecord", "botparty_streamer"} for camera in cameras):
        capture_devices = list_alsa_devices("capture")
        return [
            DoctorCheck(
                "audio:capture",
                "OK" if capture_devices else "ERROR",
                f"{len(capture_devices)} capture device(s)",
                None
                if capture_devices
                else "Connect an ALSA capture device and grant audio access.",
            )
        ]
    return []


def _custom_tts_doctor_check(config: RobotConfig) -> DoctorCheck:
    try:
        trusted_module = validate_custom_tts_module(config)
        status = "OK"
        detail = "development module" if trusted_module is None else "root-controlled module"
        fix = None
    except ValueError:
        status = "ERROR"
        detail = "untrusted module"
        fix = "Install the custom TTS module below a root-controlled path."
    return DoctorCheck("tts:custom:module", status, detail, fix)


def _tts_doctor_checks(config: RobotConfig) -> list[DoctorCheck]:
    if not config.tts.enabled:
        return []

    checks: list[DoctorCheck] = []
    if config.tts.type == "custom":
        checks.append(_custom_tts_doctor_check(config))

    tts_binaries = {
        "espeak": ("espeak_path", "espeak"),
        "festival": ("text2wave_path", "text2wave"),
        "pico": ("pico2wave_path", "pico2wave"),
        "google_cloud": ("aplay_path", "aplay"),
        "polly": ("mpg123_path", "mpg123"),
    }
    binary_spec = tts_binaries.get(config.tts.type)
    if binary_spec is not None:
        option_name, default = binary_spec
        executable = str(config.tts.options.get(option_name, default))
        available = shutil.which(executable)
        checks.append(
            DoctorCheck(
                f"tts:{config.tts.type}:binary",
                "OK" if available else "ERROR",
                available or "not found",
                None if available else f"Install the {default} executable.",
            )
        )
    if config.tts.type in {"google_cloud", "polly"}:
        consent = bool(config.tts.options.get("cloud_data_processing_accepted", False))
        checks.append(
            DoctorCheck(
                f"tts:{config.tts.type}:consent",
                "OK" if consent else "ERROR",
                "accepted" if consent else "missing",
                None
                if consent
                else "Set cloud_data_processing_accepted only after deployment approval.",
            )
        )
    if config.tts.type == "google_cloud":
        credential_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip() or str(
            config.tts.options.get("key_file") or ""
        )
        credential_ok = _private_readable_credential(credential_path)
        checks.append(
            DoctorCheck(
                "tts:google_cloud:credentials",
                "OK" if credential_ok else "ERROR",
                credential_path or "not configured",
                None
                if credential_ok
                else (
                    "Set tts.options.key_file or GOOGLE_APPLICATION_CREDENTIALS to a "
                    "private, service-readable regular file."
                ),
            )
        )
    if config.tts.type == "polly":
        access_key_file = str(config.tts.options.get("access_key_file") or "")
        secret_key_file = str(config.tts.options.get("secret_key_file") or "")
        environment_credentials = bool(
            os.getenv("AWS_ACCESS_KEY_ID", "").strip()
            and os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
        )
        file_credentials = _private_readable_credential(
            access_key_file
        ) and _private_readable_credential(secret_key_file)
        credentials_ok = environment_credentials or file_credentials
        checks.append(
            DoctorCheck(
                "tts:polly:credentials",
                "OK" if credentials_ok else "ERROR",
                "configured" if credentials_ok else "not configured",
                None
                if credentials_ok
                else (
                    "Provide private access_key_file and secret_key_file options or the "
                    "standard AWS credential environment."
                ),
            )
        )
    return checks


def _device_policy_doctor_checks(config: RobotConfig) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    for rule in systemd_device_allow_rules(config):
        device = rule.removeprefix("DeviceAllow=").rsplit(" ", 1)[0]
        if not device.startswith("/dev/"):
            continue
        exists = Path(device).exists()
        checks.append(
            DoctorCheck(
                f"device_policy:{device}",
                "OK" if exists else "ERROR",
                "present" if exists else "missing",
                None if exists else "Connect the configured device before starting the service.",
            )
        )
    return checks


def _ota_doctor_checks(config: RobotConfig) -> list[DoctorCheck]:
    if not config.ota.enabled or config.ota.public_key_file is None:
        return []
    try:
        load_public_key(config.ota.public_key_file)
        key_ok = True
    except ArtifactVerificationError:
        key_ok = False
    return [
        DoctorCheck(
            "ota:public_key",
            "OK" if key_ok else "ERROR",
            str(config.ota.public_key_file),
            None if key_ok else "Install the trusted Ed25519 OTA public key.",
        )
    ]


def _network_doctor_checks(config: RobotConfig) -> list[DoctorCheck]:
    parsed = urlsplit(config.server.api_url)
    try:
        host = parsed.hostname
        if not host:
            raise ValueError("API URL has no hostname")
        port = parsed.port or 443
        socket.getaddrinfo(host, port)
        context = ssl.create_default_context()
        with (
            socket.create_connection((host, port), timeout=5) as connection,
            context.wrap_socket(connection, server_hostname=host),
        ):
            pass
        return [DoctorCheck("api_tls", "OK", host)]
    except Exception as exc:
        return [
            DoctorCheck(
                "api_tls",
                "ERROR",
                type(exc).__name__,
                "Check DNS, system time, CA certificates and outbound HTTPS access.",
            )
        ]


def run_doctor(config: RobotConfig, *, network: bool = False) -> list[DoctorCheck]:
    checks, service_uid = _runtime_doctor_checks()
    checks.extend(_configuration_doctor_checks(config))
    checks.extend(_hardware_doctor_checks(config))
    checks.append(_state_directory_doctor_check(config, service_uid))

    cameras = [camera for camera in normalize_cameras(config) if camera.enabled]
    checks.extend(_camera_doctor_checks(config, cameras))
    checks.extend(_audio_doctor_checks(cameras))
    checks.extend(_tts_doctor_checks(config))
    checks.extend(_device_policy_doctor_checks(config))
    checks.extend(_ota_doctor_checks(config))
    if network:
        checks.extend(_network_doctor_checks(config))
    return checks


def _support_json(config: RobotConfig, value: object) -> bytes:
    serialized = json.dumps(redact_structure(value), indent=2, sort_keys=True)
    literals = tuple(
        value
        for value in (
            config.server.claim_token_value(),
            config.server.device_key_value(),
            config.server.robot_auth_token_value(),
            *config.diagnostics.redaction_literals,
        )
        if value
    )
    return redact_text(serialized, literals).encode("utf-8")


def _read_local_health(config: RobotConfig) -> dict[str, object]:
    host = os.getenv("BOTPARTY_HEALTH_HOST", "127.0.0.1").strip() or "127.0.0.1"
    if host not in {"127.0.0.1", "::1", "localhost"}:
        return {"available": False, "errorCode": "non_loopback_health_not_queried"}
    port = os.getenv("BOTPARTY_HEALTH_PORT", "9100").strip()
    url = f"http://{host}:{port}/health"
    headers: dict[str, str] = {}
    token_path = os.getenv("BOTPARTY_HEALTH_AUTH_TOKEN_FILE", "").strip()
    if token_path:
        try:
            token_file = Path(token_path)
            metadata = token_file.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
            ):
                raise OSError("health token permissions are unsafe")
            token = token_file.read_text(encoding="utf-8").strip()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        except OSError:
            return {"available": False, "errorCode": "health_token_unreadable"}
    try:
        with build_opener(_RejectRedirects).open(
            Request(url, headers=headers), timeout=1
        ) as response:
            data = response.read(256 * 1024 + 1)
        if len(data) > 256 * 1024:
            return {"available": False, "errorCode": "health_response_too_large"}
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError
        payload["available"] = True
        return payload
    except Exception as exc:
        return {"available": False, "errorCode": f"health_{type(exc).__name__.lower()}"}


def write_support_bundle(config: RobotConfig, output: Path) -> None:
    checks = run_doctor(config, network=False)
    cameras = [camera for camera in normalize_cameras(config) if camera.enabled]
    configured_state = {
        "schemaVersion": 1,
        "clientVersion": __version__,
        "requirements": _config_requirements(config),
        "devicePolicy": systemd_device_allow_rules(config),
        "cameras": [
            {
                "id": camera.id,
                "role": camera.role,
                "videoProfile": camera.video.type,
                "width": camera.camera.width,
                "height": camera.camera.height,
                "fps": camera.camera.fps,
            }
            for camera in cameras
        ],
    }
    entries: dict[str, bytes] = {
        "config.redacted.yaml": yaml.safe_dump(redacted_config(config), sort_keys=False).encode(
            "utf-8"
        ),
        "doctor.json": _support_json(config, [asdict(check) for check in checks]),
        "health.json": _support_json(config, _read_local_health(config)),
        "configuration.json": _support_json(config, configured_state),
        "runtime.json": _support_json(
            config,
            {
                "clientVersion": __version__,
                "python": platform.python_version(),
                "platform": platform.platform(),
            },
        ),
    }
    manifest = {
        "schemaVersion": 1,
        "privacy": {
            "includesMedia": False,
            "includesChat": False,
            "includesCommandValues": False,
        },
        "files": [
            {"name": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            for name, content in sorted(entries.items())
        ],
    }
    entries["bundle-manifest.json"] = _support_json(config, manifest)
    if sum(len(content) for content in entries.values()) > MAX_SUPPORT_BUNDLE_BYTES:
        raise ValueError("support bundle content exceeds 2 MiB")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=output.parent,
        prefix=f".{output.name}.",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in sorted(entries.items()):
                archive.writestr(name, content)
        temporary.chmod(0o600)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
