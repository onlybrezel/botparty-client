import asyncio
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from botparty_robot.__main__ import _load_config_from
from botparty_robot.client_commands import ClientCommandsMixin
from botparty_robot.client_state import QueuedHardwareCommand, WatchdogStats
from botparty_robot.config import (
    CameraConfig,
    CameraStreamConfig,
    RobotConfig,
    ServerConfig,
    VideoConfig,
    normalize_cameras,
    normalize_livekit_url,
)
from botparty_robot.safety import SafetyController


class _TTSStub:
    def mute(self) -> None:
        pass

    def unmute(self) -> None:
        pass

    def set_volume(self, _level: int) -> None:
        pass

    def can_handle(self) -> bool:
        return False


class _DummyCommands(ClientCommandsMixin):
    def __init__(self) -> None:
        self._tts_queue: asyncio.Queue[tuple[str, dict | None]] = asyncio.Queue(maxsize=4)
        self.tts = _TTSStub()
        self.handler = SimpleNamespace(apply_emergency_stop=lambda: self._mark_stopped())
        self.stats = SimpleNamespace(last_command_at=12.0, safety_stops=0)
        self.config = RobotConfig(server=ServerConfig(claim_token="claim-token"))
        self._safety = SafetyController()
        self._motion_deadline_handle = None
        self._motion_deadline_generation = 0
        self._hardware_safety_epoch = 0
        self._latest_motion_command_id = 0
        self._stop_calls = 0

    def _mark_stopped(self) -> None:
        self._stop_calls += 1


def test_duplicate_camera_ids_are_rejected():
    with pytest.raises(ValidationError, match="camera ids must be unique"):
        RobotConfig(
            server=ServerConfig(claim_token="claim-token"),
            cameras=[
                CameraStreamConfig(id="front"),
                CameraStreamConfig(id="front"),
            ],
        )


def test_normalize_cameras_merges_overrides():
    config = RobotConfig(
        server=ServerConfig(claim_token="claim-token"),
        camera=CameraConfig(device="/dev/video0", width=1280, height=720, fps=30),
        video=VideoConfig(type="ffmpeg", options={"preset": "veryfast"}),
        cameras=[
            CameraStreamConfig(id="front", device="/dev/video2", width=640, height=480),
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

    assert config.server.claim_token_value() == "from-env"
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


class _PipelineCommands(ClientCommandsMixin):
    def __init__(self) -> None:
        self.config = RobotConfig(server=ServerConfig(claim_token="claim-token"))
        self.handler = SimpleNamespace(
            is_motion_command=lambda command: command in {"forward", "backward", "left", "right"}
        )
        self.tts = _TTSStub()
        self.stats = WatchdogStats()
        self._tts_queue: asyncio.Queue[tuple[str, dict | None]] = asyncio.Queue(maxsize=4)
        self._processed_action_ids: deque[str] = deque(maxlen=256)
        self._command_queue: deque[QueuedHardwareCommand] = deque(maxlen=4)
        self._command_queue_event = asyncio.Event()
        self._latest_motion_command_id = 0
        self._safety = SafetyController()
        self.results: list[tuple[str, str]] = []

    def _media_operational(self) -> bool:
        return True

    def _arm_motion_deadline(self) -> None:
        return None

    def _emit_command_result(
        self,
        metadata: dict | None,
        state: str,
        code: str,
        detail: str | None = None,
    ) -> None:
        del metadata, detail
        if state != "accepted":
            self.results.append((state, code))


def test_command_pipeline_rejects_stale_and_replayed_actions() -> None:
    client = _PipelineCommands()
    stale_timestamp = time.time() * 1000 - client.config.safety.command_ttl_ms - 1
    client._process_command(
        "forward",
        None,
        stale_timestamp,
        "test",
        {"actionId": "stale-1"},
    )
    assert client.results == [("rejected", "stale_action")]
    assert list(client._command_queue) == []

    timestamp = time.time() * 1000
    client._process_command("forward", None, timestamp, "test", {"actionId": "same-id"})
    client._process_command("forward", None, timestamp, "test", {"actionId": "same-id"})
    assert client.results[-1] == ("rejected", "replayed_action")
    assert len(client._command_queue) == 1
    assert client.stats.stale_commands == 2


def test_motion_queue_is_latest_wins_and_bounded() -> None:
    client = _PipelineCommands()
    now = time.time() * 1000
    for index, command in enumerate(("forward", "left", "backward", "right"), start=1):
        client._process_command(
            command,
            index,
            now,
            "test",
            {"actionId": f"motion-{index}"},
        )
    assert len(client._command_queue) == 1
    assert client._command_queue[0].command == "right"
    assert client.stats.command_queue_drops == 3
    assert client.stats.command_queue_high_watermark == 1


def test_remote_actions_require_scope_and_report_idempotent_outcomes() -> None:
    async def _scenario() -> None:
        events: list[tuple[str, dict[str, object]]] = []

        class _Gateway:
            async def send_event(self, event: str, data: dict[str, object]) -> bool:
                events.append((event, data))
                return True

        client = _PipelineCommands()
        client._gateway = _Gateway()
        client._processed_remote_action_ids = deque(maxlen=16)
        await client._execute_action(
            {
                "actionId": "remote-1",
                "type": "restart_chat",
                "scopes": ["speak:restart"],
            }
        )
        await asyncio.sleep(0)
        assert [event[1]["state"] for event in events] == ["accepted", "completed"]

        await client._execute_action(
            {
                "actionId": "remote-1",
                "type": "restart_chat",
                "scopes": ["speak:restart"],
            }
        )
        await client._execute_action({"actionId": "remote-2", "type": "restart_chat", "scopes": []})
        await asyncio.sleep(0)
        assert events[-2][1]["code"] == "replayed_action"
        assert events[-1][1]["code"] == "missing_scope"

    asyncio.run(_scenario())
