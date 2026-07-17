"""Shared helpers for TTS profiles."""

from __future__ import annotations

import contextlib
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

_ACTIVE_PROCESSES: set[subprocess.Popen[bytes]] = set()
_ACTIVE_PROCESSES_LOCK = threading.Lock()


def make_temp_path(suffix: str) -> Path:
    return Path(tempfile.gettempdir()) / f"botparty_tts_{uuid.uuid4().hex}{suffix}"


def shell_quote(value: str) -> str:
    return shlex.quote(value)


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def run_shell(command: str, timeout_sec: float = 20.0) -> int:
    process = subprocess.Popen(
        command,
        shell=True,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with _ACTIVE_PROCESSES_LOCK:
        _ACTIVE_PROCESSES.add(process)
    try:
        return process.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            return process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            return process.wait(timeout=2)
    finally:
        with _ACTIVE_PROCESSES_LOCK:
            _ACTIVE_PROCESSES.discard(process)


def terminate_active_tts_processes() -> None:
    with _ACTIVE_PROCESSES_LOCK:
        processes = list(_ACTIVE_PROCESSES)
    for process in processes:
        if process.poll() is not None:
            continue
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        timer = threading.Timer(0.5, _kill_process_group_if_running, args=(process,))
        timer.daemon = True
        timer.start()


def _kill_process_group_if_running(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)


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
