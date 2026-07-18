from __future__ import annotations

import asyncio
import sys
import threading
from types import SimpleNamespace

import pytest

import botparty_robot.camera as camera_module
from botparty_robot.camera import CameraManager
from botparty_robot.config import CameraConfig, RobotConfig, ServerConfig, VideoConfig
from botparty_robot.video.base import BaseVideoProfile


class _Profile(BaseVideoProfile):
    def __init__(self, config: RobotConfig, mode: str) -> None:
        super().__init__(config)
        self.mode = mode
        self.disabled_called = False
        self.sdk_called = False
        self.audio_started = False
        self.process = None

    def capture_mode(self) -> str:
        return self.mode

    def has_audio(self) -> bool:
        return True

    async def run_disabled(self, running) -> None:
        del running
        self.disabled_called = True

    async def capture_sdk_frames(self, rtc, source, running, on_frame) -> None:
        del rtc, source, running
        self.sdk_called = True
        on_frame()

    async def start_audio(self, rtc, room, running) -> None:
        del rtc, room, running
        self.audio_started = True
        await asyncio.sleep(3600)

    async def spawn_ffmpeg_process(self):
        if self.process is None:
            raise RuntimeError("test process missing")
        return self.process


class _Encoding:
    def __init__(self) -> None:
        self.value = None

    def CopyFrom(self, value) -> None:
        self.value = value


class _Source:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.frames: list[object] = []

    def capture_frame(self, frame: object) -> None:
        self.frames.append(frame)


class _LocalParticipant:
    def __init__(self) -> None:
        self.published: list[tuple[object, object]] = []

    async def publish_track(self, track: object, options: object) -> None:
        self.published.append((track, options))


class _Room:
    def __init__(self) -> None:
        self.local_participant = _LocalParticipant()


def _install_fake_rtc(monkeypatch) -> list[_Source]:
    sources: list[_Source] = []

    def source(width: int, height: int) -> _Source:
        created = _Source(width, height)
        sources.append(created)
        return created

    monkeypatch.setattr(camera_module.rtc, "VideoSource", source)
    monkeypatch.setattr(
        camera_module.rtc,
        "LocalVideoTrack",
        SimpleNamespace(create_video_track=lambda name, item: (name, item)),
    )
    monkeypatch.setattr(
        camera_module.rtc,
        "TrackPublishOptions",
        lambda **kwargs: SimpleNamespace(video_encoding=_Encoding(), **kwargs),
    )
    monkeypatch.setattr(camera_module.rtc, "TrackSource", SimpleNamespace(SOURCE_CAMERA="camera"))
    monkeypatch.setattr(
        camera_module.rtc,
        "VideoEncoding",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        camera_module.rtc,
        "VideoFrame",
        lambda width, height, buffer_type, data: (width, height, buffer_type, data),
    )
    monkeypatch.setattr(camera_module.rtc, "VideoBufferType", SimpleNamespace(RGBA="rgba"))
    return sources


def _config(*, backend: str = "auto", warmup_frames: int = 0) -> RobotConfig:
    return RobotConfig(
        server=ServerConfig(claim_token="claim-token"),
        camera=CameraConfig(
            device="/dev/video2",
            width=160,
            height=120,
            fps=20,
            backend=backend,
            warmup_frames=warmup_frames,
        ),
        video=VideoConfig(type="ffmpeg", options={"publish_fps": 10}),
    )


def test_camera_manager_rejects_missing_room_and_runs_disabled_profile() -> None:
    async def scenario() -> None:
        config = _config()
        profile = _Profile(config, "none")
        manager = CameraManager(config, profile)
        with pytest.raises(RuntimeError, match="room is required"):
            await manager.run(None, None, lambda: False, lambda: False)
        await manager.run(_Room(), None, lambda: False, lambda: False)
        assert profile.disabled_called

    asyncio.run(scenario())


def test_camera_manager_sdk_mode_publishes_track_audio_and_frames(monkeypatch) -> None:
    async def scenario() -> None:
        sources = _install_fake_rtc(monkeypatch)
        config = _config()
        profile = _Profile(config, "sdk")
        manager = CameraManager(config, profile, track_name="rear", camera_id="rear")
        room = _Room()
        await manager.run(room, 900, lambda: True, lambda: True)

        assert profile.sdk_called
        assert manager.frame_count == 1
        assert manager.audio_task is None
        assert len(room.local_participant.published) == 1
        assert sources[0].width == 160

        task = manager.restart_audio(room, lambda: True)
        await asyncio.sleep(0)
        assert profile.audio_started
        assert manager.audio_task is task
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


class _FrameReader:
    def __init__(self, frames: list[bytes], partial: bytes = b"") -> None:
        self.frames = list(frames)
        self.partial = partial

    async def readexactly(self, size: int) -> bytes:
        if self.frames:
            value = self.frames.pop(0)
            assert len(value) == size
            await asyncio.sleep(0)
            return value
        raise asyncio.IncompleteReadError(self.partial, size)


class _LineReader:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = list(lines)

    async def readline(self) -> bytes:
        return self.lines.pop(0) if self.lines else b""


class _Process:
    def __init__(self, stdout=None, stderr=None, returncode: int | None = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


def test_ffmpeg_pipeline_reads_latest_frames_and_shuts_down(monkeypatch) -> None:
    async def scenario() -> None:
        sources = _install_fake_rtc(monkeypatch)
        config = _config()
        profile = _Profile(config, "ffmpeg")
        profile.process = _Process(
            stdout=_FrameReader([bytes([index]) * (160 * 120 * 4) for index in range(1, 7)]),
            stderr=_LineReader([b"diagnostic\n", b""]),
        )
        manager = CameraManager(config, profile, audio_enabled=False)
        await manager.run(_Room(), 800, lambda: True, lambda: True)

        assert manager.frame_count >= 1
        assert sources[0].frames
        assert profile.process.terminated

    asyncio.run(scenario())


def test_ffmpeg_pipeline_reports_early_stream_and_missing_stdout(monkeypatch) -> None:
    async def scenario() -> None:
        _install_fake_rtc(monkeypatch)
        config = _config()
        profile = _Profile(config, "ffmpeg")
        profile.process = _Process(stdout=_FrameReader([], b"short"))
        manager = CameraManager(config, profile, audio_enabled=False)
        with pytest.raises(RuntimeError, match="stream ended early"):
            await manager.run(_Room(), None, lambda: True, lambda: True)

        profile.process = _Process(stdout=None)
        with pytest.raises(RuntimeError, match="no stdout"):
            await manager.run(_Room(), None, lambda: True, lambda: True)

    asyncio.run(scenario())


class _Frame:
    def tobytes(self) -> bytes:
        return b"frame"


class _Capture:
    def __init__(self, device, backend=None, *, opened: bool = True) -> None:
        self.device = device
        self.backend = backend
        self.opened = opened
        self.read_count = 0
        self.released = False
        self.settings: list[tuple[object, object]] = []

    def isOpened(self) -> bool:
        return self.opened

    def set(self, key, value) -> None:
        self.settings.append((key, value))

    def get(self, key) -> float:
        return {"width": 160, "height": 120, "fps": 20}.get(key, 0)

    def getBackendName(self) -> str:
        return "TEST"

    def read(self):
        self.read_count += 1
        return (self.read_count == 1, _Frame())

    def release(self) -> None:
        self.released = True


def _fake_cv2(captures: list[_Capture], *, opened: bool = True):
    def video_capture(device, backend=None):
        cap = _Capture(device, backend, opened=opened)
        captures.append(cap)
        return cap

    return SimpleNamespace(
        VideoCapture=video_capture,
        VideoWriter_fourcc=lambda *_chars: 123,
        cvtColor=lambda frame, mode: frame,
        COLOR_BGR2RGBA="convert",
        CAP_V4L2=42,
        CAP_FFMPEG=43,
        CAP_PROP_FOURCC="fourcc",
        CAP_PROP_BUFFERSIZE="buffer",
        CAP_PROP_FRAME_WIDTH="width",
        CAP_PROP_FRAME_HEIGHT="height",
        CAP_PROP_FPS="fps",
    )


def test_opencv_pipeline_resolves_configures_reads_and_releases(monkeypatch) -> None:
    async def scenario() -> None:
        sources = _install_fake_rtc(monkeypatch)
        captures: list[_Capture] = []
        fake_cv2 = _fake_cv2(captures)
        monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
        config = _config(backend="v4l2")
        config.camera.fourcc = "mjpg"
        profile = _Profile(config, "opencv")
        manager = CameraManager(config, profile, audio_enabled=False)
        await manager.run(
            _Room(),
            None,
            lambda: not captures or captures[0].read_count < 2,
            lambda: True,
        )

        assert captures[0].device == 2
        assert captures[0].backend == 42
        assert captures[0].released
        assert manager.frame_count == 1
        assert sources[0].frames

        assert manager._resolve_device() == 2
        config.camera.device = " 3 "
        assert manager._resolve_device() == 3
        config.camera.device = "camera-name"
        assert manager._resolve_device() == "camera-name"

    asyncio.run(scenario())


def test_opencv_cancel_releases_blocking_capture_and_joins_owner(monkeypatch) -> None:
    class BlockingCapture(_Capture):
        def __init__(self, device, backend=None) -> None:
            super().__init__(device, backend)
            self.read_started = threading.Event()
            self.release_event = threading.Event()

        def read(self):
            self.read_started.set()
            self.release_event.wait(timeout=5)
            return False, _Frame()

        def release(self) -> None:
            self.released = True
            self.release_event.set()

    async def scenario() -> None:
        _install_fake_rtc(monkeypatch)
        captures: list[BlockingCapture] = []

        def video_capture(device, backend=None):
            capture = BlockingCapture(device, backend)
            captures.append(capture)
            return capture

        fake_cv2 = _fake_cv2([])
        fake_cv2.VideoCapture = video_capture
        monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
        config = _config()
        manager = CameraManager(config, _Profile(config, "opencv"), audio_enabled=False)
        task = asyncio.create_task(manager.run(_Room(), None, lambda: True, lambda: True))
        for _ in range(100):
            if captures and captures[0].read_started.is_set():
                break
            await asyncio.sleep(0.01)
        assert captures and captures[0].read_started.is_set()

        task.cancel()
        await asyncio.wait_for(task, timeout=2.5)
        assert captures[0].released
        assert not any(
            thread.name == "botparty-camera-front" and thread.is_alive()
            for thread in threading.enumerate()
        )

    asyncio.run(scenario())


def test_open_camera_failure_and_helper_state_transitions(monkeypatch) -> None:
    captures: list[_Capture] = []
    monkeypatch.setitem(sys.modules, "cv2", _fake_cv2(captures, opened=False))
    config = _config()
    manager = CameraManager(config, _Profile(config, "opencv"))
    with pytest.raises(RuntimeError, match="Could not open camera"):
        manager._open_camera()

    degraded = manager._update_adaptive_publish_rate(
        now=5,
        next_publish_at=0,
        publish_interval=0.1,
        lag_overruns=7,
        effective_publish_fps=20,
        min_publish_fps=8,
        publish_fps=20,
        stable_since=0,
    )
    assert degraded[3] == 18
    recovered = manager._update_adaptive_publish_rate(
        now=20,
        next_publish_at=20,
        publish_interval=0.1,
        lag_overruns=1,
        effective_publish_fps=10,
        min_publish_fps=8,
        publish_fps=20,
        stable_since=0,
    )
    assert recovered[3] == 11
    assert manager._maybe_log_ffmpeg_runtime(
        now=5,
        report_started_at=0,
        frames_since_report=3,
        effective_publish_fps=10,
        dropped_for_pacing=2,
        frame_width=2,
        frame_height=2,
    ) == (0, 3, 2)
    assert manager._maybe_log_ffmpeg_runtime(
        now=11,
        report_started_at=0,
        frames_since_report=11,
        effective_publish_fps=10,
        dropped_for_pacing=2,
        frame_width=2,
        frame_height=2,
    ) == (11, 0, 0)


def test_process_shutdown_escalates_and_stderr_is_drained(monkeypatch) -> None:
    async def scenario() -> None:
        config = _config()
        manager = CameraManager(config, _Profile(config, "ffmpeg"))
        process = _Process(returncode=None)

        async def timeout(_awaitable, timeout):
            del timeout
            if hasattr(_awaitable, "close"):
                _awaitable.close()
            raise asyncio.TimeoutError

        monkeypatch.setattr(camera_module.asyncio, "wait_for", timeout)
        await manager._shutdown_process(process, "ffmpeg")
        assert process.terminated
        assert process.killed

        await manager._drain_stderr(_LineReader([b"one\n", b"\xff\n", b""]))

    asyncio.run(scenario())
