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


MAX_CONFIGURATION_BYTES = 1024 * 1024


def validate_trusted_parent_chain(path: Path, *, owner_uid: int) -> None:
    """Reject symlinked or writable ancestors of a trusted file."""

    current = path.absolute().parent
    while True:
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise DeviceStateError(f"cannot inspect trusted parent directory: {current}") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise DeviceStateError(f"trusted parent must be a regular directory: {current}")
        if metadata.st_uid not in {0, owner_uid} or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise DeviceStateError(
                f"trusted parent is not owner-controlled or is writable by group/world: {current}"
            )
        if current == current.parent:
            return
        current = current.parent


def validate_trusted_code_file(path: Path, *, owner_uid: int) -> os.stat_result:
    """Validate a Python/native module before a privileged production import."""

    try:
        metadata = path.lstat()
    except OSError as exc:
        raise DeviceStateError(f"cannot inspect custom code file: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise DeviceStateError(f"custom code must be a regular non-symlink file: {path}")
    if metadata.st_uid != owner_uid or stat.S_IMODE(metadata.st_mode) & 0o022:
        raise DeviceStateError(
            f"custom code must be owned by uid {owner_uid} and not group/world writable: {path}"
        )
    validate_trusted_parent_chain(path, owner_uid=owner_uid)
    return metadata


def validate_configuration_file(
    path: Path, *, allow_public_example: bool = False
) -> os.stat_result:
    """Validate a development config or an installed root-managed config."""

    try:
        metadata = path.lstat()
    except OSError as exc:
        raise DeviceStateError(f"cannot inspect configuration file: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise DeviceStateError(f"configuration must be a regular non-symlink file: {path}")
    mode = stat.S_IMODE(metadata.st_mode)
    production_mode = os.getenv("BOTPARTY_DEPLOYMENT_MODE", "").strip().lower() == "production"
    service_owned = not production_mode and metadata.st_uid == os.geteuid() and mode == 0o600
    public_example = (
        allow_public_example
        and path.name == "config.example.yaml"
        and metadata.st_uid == os.geteuid()
        and mode in {0o400, 0o440, 0o444, 0o600, 0o640, 0o644}
    )
    root_managed = metadata.st_uid == 0 and mode == 0o640
    if root_managed:
        validate_trusted_parent_chain(path, owner_uid=0)
    if root_managed and os.geteuid() != 0:
        root_managed = metadata.st_gid == os.getegid() and os.access(path, os.R_OK)
    if not service_owned and not root_managed and not public_example:
        raise DeviceStateError(
            "production configuration must be root-owned mode 0640 with the service group"
            if production_mode
            else (
                "configuration must be service-owned mode 0600 or root-owned mode 0640 "
                "with the service group"
            )
        )
    if metadata.st_size > MAX_CONFIGURATION_BYTES:
        raise DeviceStateError("configuration exceeds 1 MiB")
    return metadata


def read_configuration_file(path: Path, *, allow_public_example: bool = False) -> bytes:
    """Read a validated configuration from one descriptor without following symlinks."""

    expected = validate_configuration_file(path, allow_public_example=allow_public_example)
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise DeviceStateError(f"cannot open configuration file: {path}") from exc
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise DeviceStateError("configuration changed while it was opened")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, MAX_CONFIGURATION_BYTES + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_CONFIGURATION_BYTES:
                raise DeviceStateError("configuration exceeds 1 MiB")
            chunks.append(chunk)
        final = os.fstat(descriptor)
        if (final.st_dev, final.st_ino, final.st_size) != (
            expected.st_dev,
            expected.st_ino,
            expected.st_size,
        ):
            raise DeviceStateError("configuration changed while it was read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def validate_private_regular_file(path: Path, *, exact_mode: int | None = None) -> os.stat_result:
    """Validate ownership, type and private permissions for service-owned state."""

    try:
        metadata = path.lstat()
    except OSError as exc:
        raise DeviceStateError(f"cannot inspect private file: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise DeviceStateError(f"private path must be a regular file, not a symlink: {path}")
    if metadata.st_uid != os.geteuid():
        raise DeviceStateError(
            f"private file owner uid {metadata.st_uid} does not match service uid {os.geteuid()}"
        )
    mode = stat.S_IMODE(metadata.st_mode)
    if exact_mode is not None and mode != exact_mode:
        raise DeviceStateError(f"private file permissions must be {exact_mode:04o}: {path}")
    if exact_mode is None and mode & 0o077:
        raise DeviceStateError(f"private file must not allow group/world access: {path}")
    return metadata


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
    validate_private_regular_file(path, exact_mode=0o600)


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
