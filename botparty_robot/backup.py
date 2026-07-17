"""Encrypted backup and transactional restore for device state."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import secrets
import stat
import tempfile
import time
import zipfile
from pathlib import Path

import yaml
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import ValidationError

from . import __version__
from .config import RobotConfig

BACKUP_HEADER = b"BOTPARTY-BACKUP-V1\0"
MAX_BACKUP_BYTES = 16 * 1024 * 1024


class BackupError(RuntimeError):
    pass


def load_backup_key(path: Path) -> bytes:
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise BackupError(f"cannot inspect backup key file: {path}") from exc
    if not stat.S_ISREG(file_stat.st_mode) or stat.S_ISLNK(file_stat.st_mode):
        raise BackupError("backup key path must be a regular file, not a symlink")
    if file_stat.st_uid != os.geteuid():
        raise BackupError("backup key file must be owned by the current service user")
    if stat.S_IMODE(file_stat.st_mode) != 0o600:
        raise BackupError("backup key file permissions must be 0600")
    try:
        key = base64.b64decode(path.read_text(encoding="ascii").strip(), validate=True)
    except (OSError, UnicodeError, ValueError) as exc:
        raise BackupError("backup key file must contain a base64-encoded 32-byte key") from exc
    if len(key) != 32:
        raise BackupError("backup key file must contain a base64-encoded 32-byte key")
    return key


def generate_backup_key(path: Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise BackupError(f"cannot create backup key file: {path}") from exc
    try:
        os.write(descriptor, base64.b64encode(AESGCM.generate_key(bit_length=256)) + b"\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def create_encrypted_backup(
    *,
    config_path: Path,
    device_key_path: Path,
    output_path: Path,
    key_path: Path,
    capability_hash: str | None = None,
) -> None:
    key = load_backup_key(key_path)
    memory = io.BytesIO()
    files = ["config.yaml", "device-key"]
    try:
        device_key = device_key_path.read_bytes()
    except OSError as exc:
        raise BackupError("could not read device key for backup") from exc
    manifest: dict[str, object] = {
        "schemaVersion": 1,
        "clientVersion": __version__,
        "createdAt": int(time.time()),
        "capabilityHash": capability_hash,
        "deviceKeySha256": hashlib.sha256(device_key).hexdigest(),
        "files": files,
    }
    custom_path = config_path.parent / "hardware_custom.py"
    if custom_path.is_file():
        files.append("hardware_custom.py")

    try:
        with zipfile.ZipFile(memory, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
            archive.writestr("config.yaml", config_path.read_bytes())
            archive.writestr("device-key", device_key)
            if custom_path.is_file():
                archive.writestr("hardware_custom.py", custom_path.read_bytes())
    except OSError as exc:
        raise BackupError("could not read device state for backup") from exc

    plaintext = memory.getvalue()
    if len(plaintext) > MAX_BACKUP_BYTES:
        raise BackupError("device backup exceeds 16 MiB")
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, BACKUP_HEADER)
    output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(BACKUP_HEADER + nonce + ciphertext)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, output_path)


def read_encrypted_backup(input_path: Path, key_path: Path) -> dict[str, bytes]:
    key = load_backup_key(key_path)
    try:
        encrypted = input_path.read_bytes()
    except OSError as exc:
        raise BackupError(f"cannot read backup: {input_path}") from exc
    if len(encrypted) > MAX_BACKUP_BYTES or not encrypted.startswith(BACKUP_HEADER):
        raise BackupError("backup header or size is invalid")
    nonce_start = len(BACKUP_HEADER)
    nonce = encrypted[nonce_start : nonce_start + 12]
    try:
        plaintext = AESGCM(key).decrypt(
            nonce,
            encrypted[nonce_start + 12 :],
            BACKUP_HEADER,
        )
    except InvalidTag as exc:
        raise BackupError("backup authentication failed") from exc

    result: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(plaintext)) as archive:
            allowed = {"manifest.json", "config.yaml", "device-key", "hardware_custom.py"}
            if len(archive.namelist()) != len(set(archive.namelist())):
                raise BackupError("backup contains duplicate entries")
            if set(archive.namelist()) - allowed:
                raise BackupError("backup contains unexpected paths")
            expanded_size = 0
            for name in archive.namelist():
                info = archive.getinfo(name)
                if info.file_size > MAX_BACKUP_BYTES:
                    raise BackupError("backup entry exceeds its size limit")
                expanded_size += info.file_size
                if expanded_size > MAX_BACKUP_BYTES:
                    raise BackupError("backup payload expands beyond its size limit")
                result[name] = archive.read(name)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise BackupError("backup payload is invalid") from exc
    try:
        manifest = json.loads(result["manifest.json"])
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BackupError("backup manifest is invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
        raise BackupError("backup schema is not supported")
    if "config.yaml" not in result or "device-key" not in result:
        raise BackupError("backup is missing required device state")
    expected_files = manifest.get("files")
    actual_files = sorted(set(result) - {"manifest.json"})
    if (
        not isinstance(expected_files, list)
        or not all(isinstance(name, str) for name in expected_files)
        or sorted(expected_files) != actual_files
    ):
        raise BackupError("backup manifest file inventory does not match its payload")
    device_key_hash = manifest.get("deviceKeySha256")
    if not isinstance(device_key_hash, str) or not secrets.compare_digest(
        device_key_hash,
        hashlib.sha256(result["device-key"]).hexdigest(),
    ):
        raise BackupError("backup device key does not match its manifest")
    return result


def restore_encrypted_backup(
    *,
    input_path: Path,
    key_path: Path,
    config_path: Path,
    device_key_path: Path,
) -> None:
    files = read_encrypted_backup(input_path, key_path)
    try:
        config_data = yaml.safe_load(files["config.yaml"])
    except (UnicodeError, yaml.YAMLError) as exc:
        raise BackupError("backup configuration is not valid YAML") from exc
    if not isinstance(config_data, dict):
        raise BackupError("backup configuration must contain a YAML object")
    try:
        RobotConfig.model_validate(config_data)
    except ValidationError as exc:
        raise BackupError("backup configuration does not match the supported schema") from exc
    try:
        device_key = files["device-key"].decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise BackupError("backup device key is invalid") from exc
    if len(device_key) != 64 or any(
        character not in "0123456789abcdef" for character in device_key
    ):
        raise BackupError("backup device key is invalid")
    if device_key_path.exists() or device_key_path.is_symlink():
        try:
            current_key = device_key_path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError) as exc:
            raise BackupError("existing device key cannot be verified") from exc
        if not secrets.compare_digest(current_key, device_key):
            raise BackupError("backup belongs to a different initialized device")
    if "hardware_custom.py" in files:
        try:
            compile(files["hardware_custom.py"], "hardware_custom.py", "exec")
        except (SyntaxError, ValueError) as exc:
            raise BackupError("backup custom adapter is not valid Python") from exc

    targets = {
        config_path: (files["config.yaml"], 0o600),
        device_key_path: (files["device-key"], 0o600),
    }
    if "hardware_custom.py" in files:
        targets[config_path.parent / "hardware_custom.py"] = (
            files["hardware_custom.py"],
            0o600,
        )

    temporaries: list[tuple[Path, Path]] = []
    previous: dict[Path, tuple[bytes, int] | None] = {}
    replaced: list[Path] = []
    try:
        for target, (content, mode) in targets.items():
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            previous[target] = (
                (target.read_bytes(), stat.S_IMODE(target.stat().st_mode))
                if target.is_file() and not target.is_symlink()
                else None
            )
            with tempfile.NamedTemporaryFile(
                dir=target.parent,
                prefix=f".{target.name}.",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(mode)
            temporaries.append((temporary, target))
        for temporary, target in temporaries:
            old = previous[target]
            if old is not None:
                backup = target.with_suffix(target.suffix + ".before-restore")
                _write_private_file(backup, old[0], 0o600)
            os.replace(temporary, target)
            replaced.append(target)
    except Exception:
        for target in reversed(replaced):
            old = previous[target]
            if old is None:
                target.unlink(missing_ok=True)
            else:
                _write_private_file(target, old[0], old[1])
        raise
    finally:
        for temporary, _target in temporaries:
            temporary.unlink(missing_ok=True)


def _write_private_file(target: Path, content: bytes, mode: int) -> None:
    with tempfile.NamedTemporaryFile(
        dir=target.parent,
        prefix=f".{target.name}.",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.chmod(mode)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
