"""Non-moving operator diagnostics and portable configuration helpers."""

from __future__ import annotations

import difflib
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

import yaml

from . import __version__
from .config import RobotConfig, normalize_cameras
from .device_state import resolve_state_directory
from .hardware import missing_hardware_dependency


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    status: str
    detail: str
    fix: str | None = None


def redacted_config(config: RobotConfig) -> dict[str, Any]:
    exported = config.model_dump(mode="json")
    server = exported["server"]
    server["claim_token"] = "${BOTPARTY_CLAIM_TOKEN}"
    server["device_key"] = None
    server["robot_auth_token"] = None
    for key in list(exported["tts"]["options"]):
        if any(secret in key.lower() for secret in ("key", "secret", "token", "password")):
            exported["tts"]["options"][key] = "${SECRET}"
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


def export_config(config: RobotConfig, output: Path) -> None:
    payload = redacted_config(config)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)
    with tempfile.NamedTemporaryFile(
        dir=output.parent,
        prefix=f".{output.name}.",
        mode="w",
        encoding="utf-8",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, output)


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

    proposed = yaml.safe_dump(raw, sort_keys=False, allow_unicode=False)
    current = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
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
    target_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=target_path.parent,
        prefix=f".{target_path.name}.",
        mode="w",
        encoding="utf-8",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(0o600)
    if target_path.exists():
        shutil.copy2(target_path, target_path.with_suffix(target_path.suffix + ".before-import"))
    os.replace(temporary, target_path)


def create_setup_config(
    output: Path,
    input_fn: Callable[[str], str] = input,
) -> None:
    hardware = input_fn("Hardware profile [none]: ").strip() or "none"
    video = input_fn("Video profile [ffmpeg]: ").strip() or "ffmpeg"
    api_url = input_fn("API URL [https://botparty.live]: ").strip() or "https://botparty.live"
    livekit_url = input_fn("LiveKit URL [wss://botparty.live]: ").strip() or "wss://botparty.live"
    raw = {
        "server": {
            "api_url": api_url,
            "livekit_url": livekit_url,
            "claim_token": "PASTE_YOUR_CLAIM_TOKEN_HERE",
        },
        "hardware": {"type": hardware, "options": {}},
        "video": {"type": video, "options": {}},
        "tts": {"enabled": False, "type": "none"},
    }
    RobotConfig.model_validate(raw)
    if output.exists():
        shutil.copy2(output, output.with_suffix(output.suffix + ".before-setup"))
    apply_imported_config(raw, output)


def run_doctor(config: RobotConfig, *, network: bool = False) -> list[DoctorCheck]:
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

    config_path = config._source_path
    if config_path is not None and config_path.exists():
        config_stat = config_path.lstat()
        config_secure = (
            stat.S_ISREG(config_stat.st_mode)
            and not stat.S_ISLNK(config_stat.st_mode)
            and config_stat.st_uid == service_uid
            and not stat.S_IMODE(config_stat.st_mode) & 0o077
        )
        checks.append(
            DoctorCheck(
                "config_permissions",
                "OK" if config_secure else "ERROR",
                f"{config_path} mode={stat.S_IMODE(config_stat.st_mode):04o}",
                None
                if config_secure
                else "Use a regular config file owned by the service user with mode 0600.",
            )
        )

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
    checks.append(
        DoctorCheck(
            "state_directory",
            "OK" if state_ok else "ERROR",
            str(state_dir),
            None
            if state_ok
            else f"Give the service user a private writable directory at {state_parent}.",
        )
    )

    for camera in normalize_cameras(config):
        device = camera.camera.device
        if isinstance(device, int):
            continue
        if camera.video.type in {"cozmo_vid", "vector_vid", "none"}:
            continue
        exists = Path(device).exists()
        readable = exists and os.access(device, os.R_OK)
        checks.append(
            DoctorCheck(
                f"camera:{camera.id}",
                "OK" if readable else "ERROR",
                device,
                None
                if readable
                else f"Check that {device} exists and the service user can read it.",
            )
        )

    if config.video.type.startswith("ffmpeg") or config.video.type == "botparty_streamer":
        ffmpeg = shutil.which("ffmpeg")
        checks.append(
            DoctorCheck(
                "ffmpeg",
                "OK" if ffmpeg else "ERROR",
                ffmpeg or "not found",
                None if ffmpeg else "Install ffmpeg with the operating system package manager.",
            )
        )

    if config.video.type == "opencv":
        try:
            opencv_available = importlib.util.find_spec("cv2") is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            opencv_available = False
        checks.append(
            DoctorCheck(
                "opencv",
                "OK" if opencv_available else "ERROR",
                "available" if opencv_available else "not installed",
                None if opencv_available else "Install botparty-robot[vision].",
            )
        )

    if network:
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
            checks.append(DoctorCheck("api_tls", "OK", host))
        except Exception as exc:
            checks.append(
                DoctorCheck(
                    "api_tls",
                    "ERROR",
                    type(exc).__name__,
                    "Check DNS, system time, CA certificates and outbound HTTPS access.",
                )
            )
    return checks


def write_support_bundle(config: RobotConfig, output: Path) -> None:
    checks = run_doctor(config, network=False)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=output.parent,
        prefix=f".{output.name}.",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "config.redacted.yaml",
                yaml.safe_dump(redacted_config(config), sort_keys=False),
            )
            archive.writestr(
                "doctor.json",
                json.dumps([asdict(check) for check in checks], indent=2),
            )
            archive.writestr(
                "runtime.json",
                json.dumps(
                    {
                        "clientVersion": __version__,
                        "python": platform.python_version(),
                        "platform": platform.platform(),
                    },
                    indent=2,
                ),
            )
        temporary.chmod(0o600)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
