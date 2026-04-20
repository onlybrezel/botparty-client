import asyncio
from pathlib import Path
from types import SimpleNamespace

from botparty_robot.__main__ import _load_config_from
from botparty_robot.client_commands import ClientCommandsMixin
from botparty_robot.config import (
    CameraConfig,
    CameraStreamConfig,
    RobotConfig,
    ServerConfig,
    VideoConfig,
    normalize_cameras,
    normalize_livekit_url,
)


class _TTSStub:
    def mute(self) -> None:
        pass

    def unmute(self) -> None:
        pass

    def set_volume(self, _level: int) -> None:
        pass


class _DummyCommands(ClientCommandsMixin):
    def __init__(self) -> None:
        self._tts_queue: asyncio.Queue[tuple[str, dict | None]] = asyncio.Queue(maxsize=4)
        self.tts = _TTSStub()
        self.handler = SimpleNamespace(emergency_stop=lambda: self._mark_stopped())
        self.stats = SimpleNamespace(last_command_at=12.0)
        self._hardware_safety_epoch = 0
        self._latest_motion_command_id = 0
        self._stop_calls = 0

    def _mark_stopped(self) -> None:
        self._stop_calls += 1


def test_normalize_cameras_merges_overrides_and_deduplicates_ids():
    config = RobotConfig(
        server=ServerConfig(claim_token="claim-token"),
        camera=CameraConfig(device="/dev/video0", width=1280, height=720, fps=30),
        video=VideoConfig(type="ffmpeg", options={"preset": "veryfast"}),
        cameras=[
            CameraStreamConfig(id="front", device="/dev/video2", width=640, height=480),
            CameraStreamConfig(id="front", device="/dev/video3"),
            CameraStreamConfig(id="rear", role="secondary"),
        ],
    )

    normalized = normalize_cameras(config)

    assert len(normalized) == 2
    assert normalized[0].id == "front"
    assert normalized[0].camera.device == "/dev/video2"
    assert normalized[0].camera.width == 640
    assert normalized[0].video.options.get("preset") == "veryfast"
    assert normalized[0].video.options.get("camera_id") == "front"
    assert normalized[1].id == "rear"


def test_tts_command_enqueues_message_payload():
    dummy = _DummyCommands()

    handled = dummy._maybe_handle_tts_command("tts:say:Hello BotParty")

    assert handled is True
    message, metadata = dummy._tts_queue.get_nowait()
    assert message == "Hello BotParty"
    assert metadata is None


def test_normalize_livekit_url_strips_rtc_suffix():
    assert normalize_livekit_url("wss://botparty.live/rtc") == "wss://botparty.live"
    assert normalize_livekit_url("wss://botparty.live/proxy/rtc/") == "wss://botparty.live/proxy"


def test_load_config_allows_claim_token_env_override(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "server:\n"
        '  api_url: "https://botparty.live"\n'
        '  livekit_url: "wss://botparty.live/rtc"\n'
        '  claim_token: "from-file"\n'
        "video:\n"
        '  type: "ffmpeg"\n'
        "hardware:\n"
        '  type: "none"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("BOTPARTY_CLAIM_TOKEN", "from-env")

    config = _load_config_from(str(config_path))

    assert config.server.claim_token == "from-env"
    assert config.server.livekit_url == "wss://botparty.live"


def test_load_config_defaults_video_to_ffmpeg_when_video_block_is_missing(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "server:\n"
        '  api_url: "https://botparty.live"\n'
        '  livekit_url: "wss://botparty.live"\n'
        '  claim_token: "from-file"\n'
        "camera:\n"
        '  device: "/dev/video0"\n'
        "hardware:\n"
        '  type: "none"\n',
        encoding="utf-8",
    )

    config = _load_config_from(str(config_path))

    assert config.video.type == "ffmpeg"


def test_load_config_maps_legacy_camera_pipeline_to_video_type(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "server:\n"
        '  api_url: "https://botparty.live"\n'
        '  livekit_url: "wss://botparty.live"\n'
        '  claim_token: "from-file"\n'
        "camera:\n"
        '  device: "/dev/video0"\n'
        '  pipeline: "libcamera"\n'
        "hardware:\n"
        '  type: "none"\n',
        encoding="utf-8",
    )

    config = _load_config_from(str(config_path))

    assert config.video.type == "ffmpeg_libcamera"


def test_trigger_hardware_stop_applies_emergency_stop_safely() -> None:
    async def _scenario() -> None:
        dummy = _DummyCommands()

        await dummy._trigger_hardware_stop("gateway_emergency_stop")

        assert dummy._stop_calls == 1
        assert dummy.stats.last_command_at == 0
        assert dummy._hardware_safety_epoch == 1
        assert dummy._latest_motion_command_id == 1

    asyncio.run(_scenario())
