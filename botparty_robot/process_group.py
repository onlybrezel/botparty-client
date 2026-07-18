"""Small async process-group primitive for multi-process media pipelines."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

DEFAULT_CHILD_OUTPUT_LIMIT = 64 * 1024
DEFAULT_CHILD_TERMINATE_TIMEOUT = 2.0


class ManagedProcessGroup:
    """Expose subprocess-like lifecycle methods for a set of child process groups."""

    def __init__(self, processes: Iterable[Any], *, stdout: Any = None) -> None:
        self.processes = tuple(processes)
        if not self.processes:
            raise ValueError("a managed process group requires at least one process")
        self.stdout = stdout
        self.stderr = asyncio.StreamReader()
        self._stderr_tasks = [
            asyncio.create_task(self._forward_stderr(process))
            for process in self.processes
            if process.stderr is not None
        ]
        self._stderr_finalizer = asyncio.create_task(self._finish_stderr())

    @property
    def returncode(self) -> int | None:
        codes = [process.returncode for process in self.processes]
        completed = [code for code in codes if code is not None]
        if not completed:
            return None
        nonzero = next((code for code in completed if code != 0), None)
        return nonzero if nonzero is not None else 0

    async def _forward_stderr(self, process: Any) -> None:
        assert process.stderr is not None
        while True:
            line = await process.stderr.readline()
            if not line:
                return
            self.stderr.feed_data(line)

    async def _finish_stderr(self) -> None:
        await asyncio.gather(*self._stderr_tasks, return_exceptions=True)
        self.stderr.feed_eof()

    def _signal(self, sig: signal.Signals) -> None:
        for process in self.processes:
            if process.returncode is not None:
                continue
            try:
                os.killpg(process.pid, sig)
            except (ProcessLookupError, PermissionError):
                with contextlib.suppress(ProcessLookupError):
                    process.send_signal(sig)

    def terminate(self) -> None:
        self._signal(signal.SIGTERM)

    def kill(self) -> None:
        self._signal(signal.SIGKILL)

    async def wait(self) -> int:
        codes = await asyncio.gather(*(process.wait() for process in self.processes))
        await self._stderr_finalizer
        nonzero = next((code for code in codes if code != 0), None)
        return int(nonzero if nonzero is not None else 0)


async def terminate_async_process(
    process: Any,
    *,
    terminate_timeout: float = 5.0,
    kill_timeout: float = 2.0,
) -> bool:
    """Stop and reap one async subprocess session with TERM/KILL escalation."""

    if process.returncode is not None:
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        await process.wait()
        return True
    process_id = getattr(process, "pid", None)
    try:
        if process_id is None:
            raise ProcessLookupError
        os.killpg(process_id, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=terminate_timeout)
        return True
    except asyncio.TimeoutError:
        pass
    try:
        if process_id is None:
            raise ProcessLookupError
        os.killpg(process_id, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    try:
        await asyncio.wait_for(process.wait(), timeout=kill_timeout)
        return True
    except asyncio.TimeoutError:
        return False


def credential_minimized_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    """Return the small non-secret environment allowed for media and TTS children."""

    environment = os.environ if source is None else source
    exact = {
        "PATH",
        "LANG",
        "LANGUAGE",
        "TZ",
        "XDG_RUNTIME_DIR",
        "PULSE_SERVER",
        "PULSE_COOKIE",
        "ALSA_CONFIG_PATH",
        "ALSA_CARD",
        "LIBCAMERA_LOG_LEVELS",
    }
    return {
        name: value
        for name, value in environment.items()
        if name in exact or name.startswith("LC_")
    }


def _terminate_process_group(
    process: subprocess.Popen[bytes],
    *,
    timeout: float = DEFAULT_CHILD_TERMINATE_TIMEOUT,
) -> None:
    """Terminate a child session and synchronously reap its leader."""

    if process.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=timeout)


def _read_bounded_stream(stream: Any, target: bytearray, limit: int) -> None:
    """Drain a pipe completely while retaining at most ``limit`` bytes."""

    try:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            remaining = limit - len(target)
            if remaining > 0:
                target.extend(chunk[:remaining])
    finally:
        stream.close()


def run_sandboxed(
    command: list[str],
    *,
    timeout: float,
    check: bool = False,
    capture_output: bool = False,
    text: bool = False,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    output_limit: int = DEFAULT_CHILD_OUTPUT_LIMIT,
) -> subprocess.CompletedProcess[Any]:
    """Run a bounded, credential-minimized child in its own process session."""

    if not command or any(not isinstance(argument, str) for argument in command):
        raise ValueError("child command must be a non-empty string argument list")
    if timeout <= 0:
        raise ValueError("child timeout must be positive")
    if output_limit < 0:
        raise ValueError("child output limit must not be negative")

    stdout_pipe = subprocess.PIPE if capture_output else subprocess.DEVNULL
    stderr_pipe = subprocess.PIPE if capture_output else subprocess.DEVNULL
    process = subprocess.Popen(
        command,
        shell=False,
        start_new_session=True,
        stdout=stdout_pipe,
        stderr=stderr_pipe,
        cwd=cwd,
        env=credential_minimized_environment() if env is None else env,
    )
    stdout = bytearray()
    stderr = bytearray()
    readers: list[threading.Thread] = []
    if capture_output:
        assert process.stdout is not None
        assert process.stderr is not None
        readers = [
            threading.Thread(
                target=_read_bounded_stream,
                args=(process.stdout, stdout, output_limit),
                daemon=True,
            ),
            threading.Thread(
                target=_read_bounded_stream,
                args=(process.stderr, stderr, output_limit),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()

    try:
        return_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(process)
        for reader in readers:
            reader.join(timeout=DEFAULT_CHILD_TERMINATE_TIMEOUT)
        raise subprocess.TimeoutExpired(command, timeout, bytes(stdout), bytes(stderr)) from exc
    finally:
        if process.poll() is not None:
            for reader in readers:
                reader.join(timeout=DEFAULT_CHILD_TERMINATE_TIMEOUT)

    stdout_bytes = bytes(stdout)
    stderr_bytes = bytes(stderr)
    stdout_value: bytes | str = stdout_bytes
    stderr_value: bytes | str = stderr_bytes
    if text:
        stdout_value = stdout_bytes.decode("utf-8", errors="replace")
        stderr_value = stderr_bytes.decode("utf-8", errors="replace")
    result = subprocess.CompletedProcess(command, return_code, stdout_value, stderr_value)
    if check and return_code != 0:
        raise subprocess.CalledProcessError(
            return_code,
            command,
            output=stdout_value,
            stderr=stderr_value,
        )
    return result
