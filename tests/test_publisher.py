from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import ClassVar

import pytest
from pydantic import SecretStr

import botparty_robot.publisher as publisher_module
from botparty_robot.config import CameraConfig, RobotConfig, ServerConfig, VideoConfig
from botparty_robot.publisher import LiveKitPublisherManager


class _Profile:
    profile_name = "botparty_streamer"

    def __init__(self) -> None:
        self.options: dict[str, object] = {
            "direct_audio_enabled": True,
            "video_codec": "h264_v4l2m2m",
        }
        self.process = None
        self.audio_started = 0

    def has_audio(self) -> bool:
        return True

    def detect_default_h264_codec(self) -> str:
        return "h264_v4l2m2m"

    def output_fps(self) -> float:
        return 20.0

    async def start_audio(self, rtc, room, running) -> None:
        del rtc, room, running
        self.audio_started += 1

    async def spawn_livekit_process(self, **kwargs):
        del kwargs
        if self.process is None:
            raise RuntimeError("process missing")
        return self.process


class _Stream:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = list(lines)

    async def readline(self) -> bytes:
        return self.lines.pop(0) if self.lines else b""


class _Process:
    def __init__(self, returncode: int | None = 0, stream: _Stream | None = None) -> None:
        self.returncode = returncode
        self.stderr = stream
        self.stdout = None
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


def _manager(
    profile: _Profile | None = None,
    *,
    token: str | None = "video-token",
    audio_token: str | None = "audio-token",
    url: str | None = "wss://botparty.live",
    audio_enabled: bool = True,
) -> LiveKitPublisherManager:
    config = RobotConfig(
        server=ServerConfig(claim_token="claim-token"),
        camera=CameraConfig(width=320, height=240, fps=20),
        video=VideoConfig(type="botparty_streamer"),
    )
    return LiveKitPublisherManager(
        config,
        profile or _Profile(),  # type: ignore[arg-type]
        token_fn=lambda: token,
        audio_token_fn=lambda: audio_token,
        livekit_url_fn=lambda: url,
        audio_enabled=audio_enabled,
    )


def test_direct_publisher_frame_counter_adds_deltas_across_restarts() -> None:
    manager = object.__new__(LiveKitPublisherManager)
    manager._frame_count = 0
    manager._run_last_raw_frame_count = 0

    manager._record_raw_frame_count(100)
    assert manager.frame_count == 100

    manager._run_last_raw_frame_count = 0
    manager._record_raw_frame_count(1)
    manager._record_raw_frame_count(2)
    assert manager.frame_count == 102


def test_direct_publisher_frame_counter_handles_in_process_reset() -> None:
    manager = object.__new__(LiveKitPublisherManager)
    manager._frame_count = 0
    manager._run_last_raw_frame_count = 0

    for raw in (10, 11, 1, 2):
        manager._record_raw_frame_count(raw)

    assert manager.frame_count == 13


def test_restart_audio_requires_a_distinct_connected_audio_room() -> None:
    async def scenario() -> None:
        profile = _Profile()
        manager = _manager(profile)
        assert manager.audio_task is None
        assert manager.restart_audio(SimpleNamespace(), lambda: True) is None

        manager._audio_room = SimpleNamespace()
        task = manager.restart_audio(SimpleNamespace(), lambda: True)
        assert task is not None
        await task
        assert profile.audio_started == 1

        manager._audio_enabled = False
        assert manager.restart_audio(SimpleNamespace(), lambda: True) is None

    asyncio.run(scenario())


def test_publisher_retry_policies_are_bounded_and_fail_closed() -> None:
    profile = _Profile()
    manager = _manager(profile)
    error = RuntimeError("encoder exited")
    assert manager._should_retry_without_direct_audio(error, 1, False)
    assert not manager._should_retry_without_direct_audio(error, 1, True)
    assert not manager._should_retry_without_direct_audio(error, 9, False)
    assert not manager._should_retry_without_direct_audio(RuntimeError("token missing"), 1, False)
    profile.options["direct_audio_enabled"] = False
    assert not manager._should_retry_without_direct_audio(error, 1, False)
    profile.options["direct_audio_enabled"] = True
    profile.profile_name = "ffmpeg"
    assert not manager._should_retry_without_direct_audio(error, 1, False)

    profile.profile_name = "botparty_streamer"
    assert manager._should_retry_with_libx264(error, 1, False)
    assert not manager._should_retry_with_libx264(error, 1, True)
    assert not manager._should_retry_with_libx264(error, 9, False)
    assert not manager._should_retry_with_libx264(RuntimeError("permission denied"), 1, False)
    profile.options["video_codec"] = "libx264"
    assert not manager._should_retry_with_libx264(error, 1, False)


def test_run_applies_audio_then_codec_fallback_once(monkeypatch) -> None:
    async def scenario() -> None:
        profile = _Profile()
        manager = _manager(profile)
        attempts = 0

        async def run_once(*_args) -> None:
            nonlocal attempts
            attempts += 1
            manager._started_at = time.monotonic()
            if attempts <= 2:
                raise RuntimeError("publisher exited")

        monkeypatch.setattr(manager, "_run_once", run_once)
        await manager.run(None, 800, lambda: True, lambda: True)
        assert attempts == 3
        assert profile.options["direct_audio_enabled"] is False
        assert profile.options["video_codec"] == "libx264"

        attempts = 0

        async def missing(*_args) -> None:
            manager._started_at = time.monotonic()
            raise RuntimeError("token missing")

        monkeypatch.setattr(manager, "_run_once", missing)
        with pytest.raises(RuntimeError, match="token missing"):
            await manager.run(None, None, lambda: True, lambda: True)

    asyncio.run(scenario())


class _AudioRoom:
    instances: ClassVar[list[_AudioRoom]] = []
    fail_connect = False

    def __init__(self) -> None:
        self.connected = False
        self.disconnected = False
        self.instances.append(self)

    async def connect(self, url: str, token: str) -> None:
        del url, token
        if self.fail_connect:
            raise OSError("connect failed")
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True


@pytest.mark.parametrize("audio_token", [None, "video-token"])
def test_run_once_skips_unsafe_audio_tokens(audio_token) -> None:
    async def scenario() -> None:
        profile = _Profile()
        profile.process = _Process(returncode=0)
        manager = _manager(profile, audio_token=audio_token)
        await manager._run_once(800, lambda: True, lambda: True)
        assert profile.process.terminated
        assert profile.audio_started == 0

    asyncio.run(scenario())


def test_run_once_connects_separate_audio_and_cleans_every_owner(monkeypatch) -> None:
    async def scenario() -> None:
        _AudioRoom.instances.clear()
        _AudioRoom.fail_connect = False
        monkeypatch.setattr(publisher_module.rtc, "Room", _AudioRoom)
        profile = _Profile()
        profile.process = _Process(
            returncode=0,
            stream=_Stream([b"frame=3\n", b"progress=end\n", b""]),
        )
        manager = _manager(profile)
        await manager._run_once(700, lambda: True, lambda: True)
        await asyncio.sleep(0)

        assert profile.process.terminated
        assert profile.audio_started == 1
        assert _AudioRoom.instances[0].connected
        assert _AudioRoom.instances[0].disconnected
        assert manager.audio_task is None
        assert manager.frame_count == 3

    asyncio.run(scenario())


def test_run_once_handles_audio_setup_failure_and_nonzero_exit(monkeypatch) -> None:
    async def scenario() -> None:
        _AudioRoom.fail_connect = True
        monkeypatch.setattr(publisher_module.rtc, "Room", _AudioRoom)
        profile = _Profile()
        profile.process = _Process(
            returncode=7,
            stream=_Stream([b"fatal encoder error\n", b""]),
        )
        manager = _manager(profile)
        with pytest.raises(RuntimeError, match="code 7"):
            await manager._run_once(None, lambda: True, lambda: True)
        assert manager._audio_room is None
        assert profile.process.terminated

        missing = _manager(profile, token="")
        with pytest.raises(RuntimeError, match="URL or token is missing"):
            await missing._run_once(None, lambda: True, lambda: True)

    asyncio.run(scenario())


def test_log_parser_covers_progress_tracks_levels_and_diagnostics(caplog) -> None:
    manager = _manager(audio_enabled=False)
    manager._started_at = time.monotonic() - 5
    manager._last_reported_at = time.monotonic() - 2

    for line in (
        "frame=10",
        "fps=19.9",
        "bitrate=900kbits/s",
        "speed=1.0x",
        "out_time=00:00:01",
        "drop_frames=2",
        "dup_frames=1",
        "progress=continue",
        'found source {"mimeType": "video/H264"}',
        'found source {"mimeType": "video/H264"}',
        '"msg"="published track" "name"="camera" "source"="CAMERA"',
        'published track {"name": "mic", "source": "MICROPHONE"}',
        '"msg"="participant sid update"',
        "handling subscribed quality update",
        '"level"=8 "msg"="failure"',
        '"level"=4 "msg"="warning"',
        '"level"=0 "msg"="info"',
        '"level"=-1 "msg"="debug"',
        "fatal encoder failure",
        "ordinary publisher message",
    ):
        manager._handle_log_line(line)

    assert manager.frame_count == 10
    assert manager._source_mime_types == ["video/H264"]
    assert manager._published_tracks == ["camera", "microphone"]
    assert manager.video_track_published is True
    assert manager._parse_ffmpeg_progress_pair("not-progress=value") is None
    assert manager._parse_ffmpeg_progress_pair("=value") is None
    assert manager._parse_ffmpeg_progress_pair("plain") is None
    assert manager._parse_ffmpeg_progress_int("frame=12", "frame") == 12
    assert manager._parse_ffmpeg_progress_int("frame=x", "frame") is None
    assert manager._parse_ffmpeg_progress_float("fps=12.5", "fps") == 12.5
    assert manager._parse_ffmpeg_progress_float("fps=x", "fps") is None
    assert "fatal encoder failure" in manager._format_nonzero_exit(4)

    empty = _manager(audio_enabled=False)
    assert empty._format_nonzero_exit(3) == "publisher exited with code 3"
    empty._recent_log_lines.extend(["one", "two", "three"])
    assert empty._format_nonzero_exit(3).endswith("two | three")
    assert "failure" in caplog.text


def test_log_drain_runtime_stats_and_exit_summaries(monkeypatch) -> None:
    manager = _manager(audio_enabled=False)

    async def drain() -> None:
        await manager._drain_logs(_Stream([b"frame=4\n", b"progress=end\n", b""]))

    asyncio.run(drain())
    assert manager.frame_count == 4

    times = iter([20.0, 40.0, 60.0, 80.0, 100.0])
    monkeypatch.setattr(publisher_module.time, "monotonic", lambda: next(times))
    manager._started_at = 1
    manager._last_reported_at = 0
    manager._last_reported_frame_count = 0
    manager._last_ffmpeg_progress_at = 0
    manager._ffmpeg_progress.update({"fps": "20", "bitrate": "800k"})
    manager._log_runtime_stats_if_due()

    manager._frame_count = 0
    manager._last_reported_at = 20
    manager._ffmpeg_progress.clear()
    manager._log_runtime_stats_if_due()

    manager._last_ffmpeg_progress_at = 59
    manager._last_reported_at = 50
    manager._log_runtime_stats_if_due()

    manager._frame_count = 2
    manager._published_tracks = ["camera"]
    manager._source_mime_types = ["video/H264"]
    manager._log_exit_summary()
    manager._frame_count = 0
    manager._log_exit_summary()
    manager._started_at = 0
    manager._log_exit_summary()


def test_child_output_is_bounded_and_redacted_before_any_buffer_or_log(caplog) -> None:
    claim = "opaque-claim-value-for-redaction"
    robot = "opaque-robot-value-for-redaction"
    manager = _manager(audio_enabled=False)
    manager.config.server.claim_token = SecretStr(claim)
    manager.config.server.robot_auth_token = SecretStr(robot)
    raw = (
        f"fatal token={claim} robot={robot} "
        "eyJabcdefgh.ijklmnop.qrstuvwx " + "x" * (1024 * 1024)  # secret-scan: allow-test-fixture
    ).encode()

    sanitized = manager._sanitize_log_line(raw)
    manager._handle_log_line(sanitized)

    assert len(sanitized) <= 4096
    assert claim not in sanitized
    assert robot not in sanitized
    assert claim not in caplog.text
    assert robot not in caplog.text
    assert all(claim not in line and robot not in line for line in manager._recent_log_lines)
