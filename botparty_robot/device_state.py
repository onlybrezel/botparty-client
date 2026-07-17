"""Stable, permission-checked device state storage."""

from __future__ import annotations

import fcntl
import os
import secrets
import stat
from pathlib import Path

from .config import StateConfig


class DeviceStateError(RuntimeError):
    pass


def resolve_state_directory(config: StateConfig) -> Path:
    if config.directory is not None:
        return config.directory.expanduser().resolve()
    configured = os.getenv("BOTPARTY_STATE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    xdg_state = os.getenv("XDG_STATE_HOME", "").strip()
    if xdg_state:
        return (Path(xdg_state).expanduser() / "botparty").resolve()
    return (Path.home() / ".local" / "state" / "botparty").resolve()


def _validate_key_file(path: Path) -> None:
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise DeviceStateError(f"cannot inspect device key file: {path}") from exc
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise DeviceStateError(f"device key path must be a regular file, not a symlink: {path}")
    if file_stat.st_uid != os.geteuid():
        raise DeviceStateError(
            f"device key owner uid {file_stat.st_uid} does not match service uid {os.geteuid()}"
        )
    if stat.S_IMODE(file_stat.st_mode) != 0o600:
        raise DeviceStateError(f"device key permissions must be 0600: {path}")


def _read_key(path: Path) -> str:
    _validate_key_file(path)
    try:
        value = path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise DeviceStateError(f"cannot read device key file: {path}") from exc
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise DeviceStateError(f"device key file has invalid content: {path}")
    return value


def load_or_create_device_key(config: StateConfig) -> tuple[str, Path]:
    state_dir = resolve_state_directory(config)
    try:
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory_stat = state_dir.stat()
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise DeviceStateError(f"state path is not a directory: {state_dir}")
        if directory_stat.st_uid != os.geteuid():
            raise DeviceStateError(
                f"state directory owner uid {directory_stat.st_uid} does not match service uid "
                f"{os.geteuid()}"
            )
        if stat.S_IMODE(directory_stat.st_mode) & 0o077:
            raise DeviceStateError("state directory permissions must not allow group/world access")
    except OSError as exc:
        raise DeviceStateError(f"cannot create state directory: {state_dir}") from exc

    key_path = state_dir / config.device_key_file
    lock_path = state_dir / ".device-key.lock"
    lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if key_path.exists() or key_path.is_symlink():
            return _read_key(key_path), key_path

        key = secrets.token_hex(32)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            key_fd = os.open(key_path, flags, 0o600)
        except OSError as exc:
            raise DeviceStateError(f"cannot create device key file: {key_path}") from exc
        try:
            os.write(key_fd, f"{key}\n".encode("ascii"))
            os.fsync(key_fd)
        finally:
            os.close(key_fd)
        _validate_key_file(key_path)
        return key, key_path
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
