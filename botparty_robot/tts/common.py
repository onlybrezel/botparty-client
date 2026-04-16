"""Shared helpers for TTS profiles."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any


def make_temp_path(suffix: str) -> Path:
    return Path(tempfile.gettempdir()) / f"botparty_tts_{uuid.uuid4().hex}{suffix}"


def shell_quote(value: str) -> str:
    return shlex.quote(value)


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def run_shell(command: str) -> int:
    return subprocess.run(command, shell=True, check=False).returncode


def write_text_file(message: str, suffix: str = ".txt") -> Path:
    path = make_temp_path(suffix)
    path.write_text(message, encoding="utf-8")
    return path


def write_bytes_file(data: bytes, suffix: str) -> Path:
    path = make_temp_path(suffix)
    path.write_bytes(data)
    return path


def _read_secret_file(path: str) -> str:
    candidate = Path(path).expanduser()
    try:
        value = candidate.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    return value


def getenv_or_option(options: dict[str, Any], key: str, env_name: str, default: str = "") -> str:
    value = options.get(key)
    if isinstance(value, str) and value:
        return value

    option_file = options.get(f"{key}_file")
    if isinstance(option_file, str) and option_file.strip():
        secret = _read_secret_file(option_file)
        if secret:
            return secret

    env_value = os.getenv(env_name, "")
    if env_value:
        return env_value

    env_file = os.getenv(f"{env_name}_FILE", "")
    if env_file:
        secret = _read_secret_file(env_file)
        if secret:
            return secret

    return default
