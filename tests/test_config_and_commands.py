import asyncio

from botparty_robot.client_commands import ClientCommandsMixin
from botparty_robot.config import CameraConfig, CameraStreamConfig, RobotConfig, ServerConfig, VideoConfig, normalize_cameras


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
