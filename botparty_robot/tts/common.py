"""Shared helpers for TTS profiles."""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ..process_group import credential_minimized_environment

_ACTIVE_PROCESSES: set[subprocess.Popen[bytes]] = set()
_ACTIVE_PROCESSES_LOCK = threading.Lock()


def make_temp_path(suffix: str) -> Path:
    descriptor, name = tempfile.mkstemp(prefix="botparty_tts_", suffix=suffix)
    os.fchmod(descriptor, 0o600)
    os.close(descriptor)
    return Path(name)


def command_exists(command: str) -> bool:
    return shutil.which(command) is not None


def _register_process(process: subprocess.Popen[bytes]) -> None:
    with _ACTIVE_PROCESSES_LOCK:
        _ACTIVE_PROCESSES.add(process)


def _unregister_process(process: subprocess.Popen[bytes]) -> None:
    with _ACTIVE_PROCESSES_LOCK:
        _ACTIVE_PROCESSES.discard(process)


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=2)


def run_process(command: list[str], timeout_sec: float = 20.0) -> int:
    if not command or any(not isinstance(argument, str) for argument in command):
        raise ValueError("TTS process command must be a non-empty string argument list")
    process = subprocess.Popen(
        command,
        shell=False,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=credential_minimized_environment(),
    )
    _register_process(process)
    try:
        return_code = process.wait(timeout=timeout_sec)
        if return_code != 0:
            raise subprocess.CalledProcessError(return_code, command)
        return return_code
    except subprocess.TimeoutExpired:
        _terminate_process(process)
        raise TimeoutError(f"TTS process timed out: {command[0]}") from None
    finally:
        _unregister_process(process)


def run_pipeline(commands: list[list[str]], timeout_sec: float = 20.0) -> int:
    if not commands or any(not command for command in commands):
        raise ValueError("TTS pipeline requires non-empty commands")
    processes: list[subprocess.Popen[bytes]] = []
    previous_stdout = None
    try:
        for index, command in enumerate(commands):
            process = subprocess.Popen(
                command,
                shell=False,
                start_new_session=True,
                stdin=previous_stdout,
                stdout=subprocess.PIPE if index + 1 < len(commands) else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=credential_minimized_environment(),
            )
            if previous_stdout is not None:
                previous_stdout.close()
            previous_stdout = process.stdout
            processes.append(process)
            _register_process(process)

        deadline = time.monotonic() + timeout_sec
        for process in reversed(processes):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout_sec)
            return_code = process.wait(timeout=remaining)
            if return_code != 0:
                raise subprocess.CalledProcessError(return_code, process.args)
        return 0
    except subprocess.TimeoutExpired:
        for process in processes:
            _terminate_process(process)
        raise TimeoutError("TTS pipeline timed out") from None
    finally:
        if previous_stdout is not None:
            previous_stdout.close()
        for process in processes:
            _unregister_process(process)


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
    with path.open("w", encoding="utf-8") as handle:
        handle.write(message)
    return path


def write_bytes_file(data: bytes, suffix: str) -> Path:
    path = make_temp_path(suffix)
    with path.open("wb") as handle:
        handle.write(data)
    return path


def _read_secret_file(path: str) -> str:
    candidate = Path(path).expanduser()
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(candidate, flags)
        try:
            metadata = os.fstat(descriptor)
            mode = stat.S_IMODE(metadata.st_mode)
            if not stat.S_ISREG(metadata.st_mode):
                return ""
            if metadata.st_uid not in {0, os.geteuid()} or mode & 0o027:
                return ""
            if metadata.st_size > 64 * 1024:
                return ""
            with os.fdopen(descriptor, "r", encoding="utf-8", closefd=False) as handle:
                return handle.read(64 * 1024 + 1).strip()
        finally:
            os.close(descriptor)
    except (OSError, UnicodeError):
        return ""


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
