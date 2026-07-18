from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from botparty_robot.process_group import run_sandboxed


def test_sandboxed_runner_minimizes_environment_and_bounds_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOTPARTY_CLAIM_TOKEN", "canary-secret")
    result = run_sandboxed(
        [
            sys.executable,
            "-c",
            (
                "import os,sys; "
                "sys.stdout.write(os.getenv('BOTPARTY_CLAIM_TOKEN', 'absent') + '\\n'); "
                "sys.stderr.write('x' * 10000)"
            ),
        ],
        timeout=3,
        capture_output=True,
        text=True,
        output_limit=128,
    )

    assert result.returncode == 0
    assert result.stdout == "absent\n"
    assert result.stderr == "x" * 128


def test_sandboxed_runner_kills_and_reaps_timed_out_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "child.pid"
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_sandboxed(
            [
                sys.executable,
                "-c",
                (
                    "import pathlib,subprocess,sys,time; "
                    "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                    f"pathlib.Path({str(marker)!r}).write_text(str(child.pid)); "
                    "time.sleep(30)"
                ),
            ],
            timeout=0.2,
        )

    child_pid = int(marker.read_text(encoding="utf-8"))
    assert time.monotonic() - started < 5
    try:
        os.kill(child_pid, 0)
    except ProcessLookupError:
        return

    # Minimal containers may leave an orphaned descendant as a PID-1-owned
    # zombie. It has exited and holds no descriptors or executable resources.
    state = Path(f"/proc/{child_pid}/stat").read_text(encoding="ascii").split()[2]
    assert state == "Z"
