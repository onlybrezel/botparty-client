import asyncio
import contextlib
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from botparty_robot.__main__ import ConfigLoadError, _load_config_from
from botparty_robot.client_commands import ClientCommandsMixin
from botparty_robot.client_state import QueuedHardwareCommand, QueuedTTSCommand, WatchdogStats
from botparty_robot.command_queue import HardwareCommandQueue
from botparty_robot.config import (
    DEFAULT_OTA_MANIFEST_URL,
    DEFAULT_OTA_PUBLIC_KEY_FILE,
    DEFAULT_OTA_STATE_DIRECTORY,
    CameraConfig,
    CameraStreamConfig,
    DiagnosticsConfig,
    RobotConfig,
    ServerConfig,
    TTSConfig,
    VideoConfig,
    normalize_cameras,
    normalize_livekit_url,
)
from botparty_robot.hardware.base import HardwareCapabilities
from botparty_robot.remote_actions import RemoteActionExecutor
from botparty_robot.safety import HardwareCommandCancelled, SafetyController


class _TTSStub:
    def cancel_active(self) -> None:
        pass

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
        self._tts_queue: asyncio.Queue[QueuedTTSCommand] = asyncio.Queue(maxsize=4)
        self.tts = _TTSStub()
        self.handler = SimpleNamespace(apply_emergency_stop=lambda: self._mark_stopped())
        self.stats = WatchdogStats(last_command_at=12.0)
        self.config = RobotConfig(server=ServerConfig(claim_token="claim-token"))
        self._safety = SafetyController()
        self._motion_deadline_handle = None
        self._motion_deadline_generation = 0
        self._hardware_safety_epoch = 0
        self._latest_motion_command_id = 0
        self._hardware_commands = HardwareCommandQueue(4)
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
        video=VideoConfig(type="ffmpeg", options={"thread_queue_size": 4}),
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
    assert normalized[0].video.options.get("thread_queue_size") == 4
    assert normalized[0].video.options.get("camera_id") == "front"
    assert normalized[1].id == "rear"


def test_tts_command_enqueues_message_payload():
    dummy = _DummyCommands()

    handled, outcome = dummy._maybe_handle_tts_command("tts:say:Hello BotParty")

    assert handled is True
    assert outcome == "accepted"
    queued = dummy._tts_queue.get_nowait()
    assert queued.message == "Hello BotParty"
    assert queued.metadata is None


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
    config_path.chmod(0o600)
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
    config_path.chmod(0o600)

    config = _load_config_from(str(config_path))

    assert config.video.type == "ffmpeg"


def test_ota_defaults_are_prefilled_but_disabled():
    config = RobotConfig(server=ServerConfig(claim_token="claim-token"))

    assert config.ota.enabled is False
    assert config.ota.manifest_url == DEFAULT_OTA_MANIFEST_URL
    assert config.ota.public_key_file == DEFAULT_OTA_PUBLIC_KEY_FILE
    assert config.ota.state_directory == DEFAULT_OTA_STATE_DIRECTORY


def test_load_config_applies_ota_service_overrides(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        'server:\n  claim_token: "from-file"\nota:\n  enabled: false\n',
        encoding="utf-8",
    )
    config_path.chmod(0o600)
    monkeypatch.setenv("BOTPARTY_OTA_MANIFEST_URL", "https://updates.example/manifest.json")
    monkeypatch.setenv("BOTPARTY_OTA_PUBLIC_KEY_FILE", str(tmp_path / "release.pub"))
    monkeypatch.setenv("BOTPARTY_OTA_STATE_DIR", str(tmp_path / "ota"))

    config = _load_config_from(str(config_path))

    assert config.ota.manifest_url == "https://updates.example/manifest.json"
    assert config.ota.public_key_file == tmp_path / "release.pub"
    assert config.ota.state_directory == tmp_path / "ota"


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
    config_path.chmod(0o600)

    config = _load_config_from(str(config_path))

    assert config.video.type == "ffmpeg_libcamera"
    assert config._legacy_migration_used is True


def test_production_mode_rejects_service_owned_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "server:\n  claim_token: test-claim-token\n",
        encoding="utf-8",
    )
    config_path.chmod(0o600)
    monkeypatch.setenv("BOTPARTY_DEPLOYMENT_MODE", "production")

    with pytest.raises(ConfigLoadError, match="root-owned mode 0640"):
        _load_config_from(str(config_path), persist_device_key=False)


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
            is_motion_command=lambda command: command in {"forward", "backward", "left", "right"},
            supports_command=lambda command: command in {"forward", "backward", "left", "right"},
            capabilities=lambda: HardwareCapabilities(
                commands=("forward", "backward", "left", "right"),
                motion_commands=("forward", "backward", "left", "right"),
                safe_stop=True,
                close=True,
                support_level="supported",
            ),
        )
        self.tts = _TTSStub()
        self.stats = WatchdogStats()
        self._tts_queue: asyncio.Queue[QueuedTTSCommand] = asyncio.Queue(maxsize=4)
        self._processed_action_ids: deque[str] = deque(maxlen=256)
        self._hardware_commands = HardwareCommandQueue(4)
        self._latest_motion_command_id = 0
        self._safety = SafetyController()
        self._update_manager = None
        self._update_in_progress = False
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


class _TTSPipelineCommands(_PipelineCommands):
    def _emit_command_result(
        self,
        metadata: dict | None,
        state: str,
        code: str,
        detail: str | None = None,
    ) -> None:
        del metadata, detail
        self.results.append((state, code))


class _ActionPipelineCommands(_TTSPipelineCommands):
    def __init__(self) -> None:
        super().__init__()
        self.remote_results: list[tuple[str, str]] = []

    def _emit_remote_action_result(self, action_id: str, state: str, code: str) -> None:
        del action_id
        self.remote_results.append((state, code))


class _LoopTTSStub(_TTSStub):
    delay_ms = 0
    last_rejection_code: str | None = None

    def can_handle(self) -> bool:
        return True

    def should_speak(self, message: str, metadata: dict | None) -> bool:
        del metadata
        if "https://" in message:
            self.last_rejection_code = "tts_url_blocked"
            return False
        self.last_rejection_code = None
        return True

    def run_say(self, message: str, metadata: dict | None) -> None:
        del metadata
        if message == "provider-error":
            raise RuntimeError("provider unavailable")


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
    assert client._hardware_commands.pending() == ()

    timestamp = time.time() * 1000
    client._process_command("forward", None, timestamp, "test", {"actionId": "same-id"})
    client._process_command("forward", None, timestamp, "test", {"actionId": "same-id"})
    assert client.results[-1] == ("rejected", "replayed_action")
    assert len(client._hardware_commands) == 1
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
    assert len(client._hardware_commands) == 1
    assert client._hardware_commands.pending()[0].command == "right"
    assert client.stats.command_queue_drops == 3
    assert client.stats.command_queue_high_watermark == 1


def test_motion_value_schema_rejects_out_of_range_before_queueing() -> None:
    client = _PipelineCommands()
    client._process_command(
        "forward",
        101,
        time.time() * 1000,
        "test",
        {"actionId": "invalid-motion-value"},
    )

    assert client.results == [("rejected", "invalid_command_value")]
    assert client._hardware_commands.pending() == ()


def test_command_validation_preserves_replay_and_received_counter_semantics() -> None:
    now = time.time() * 1000
    client = _PipelineCommands()

    client._process_command(
        "forward",
        101,
        now,
        "test",
        {"actionId": "invalid-once"},
    )
    client._process_command(
        "forward",
        1,
        now,
        "test",
        {"actionId": "invalid-once"},
    )

    assert client.results == [
        ("rejected", "invalid_command_value"),
        ("rejected", "replayed_action"),
    ]
    assert client.stats.stale_commands == 1
    assert client.stats.commands_received == 0

    unsupported = _PipelineCommands()
    unsupported._process_command(
        "not-supported",
        None,
        now,
        "test",
        {"actionId": "unsupported-once"},
    )
    assert unsupported.results == [("rejected", "unsupported_command")]
    assert unsupported.stats.commands_received == 1


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
                "type": "restart_tts",
                "scopes": ["speak:restart"],
            }
        )
        await asyncio.sleep(0)
        assert [event[1]["state"] for event in events] == ["accepted", "completed"]

        await client._execute_action(
            {
                "actionId": "remote-1",
                "type": "restart_tts",
                "scopes": ["speak:restart"],
            }
        )
        await client._execute_action({"actionId": "remote-2", "type": "restart_tts", "scopes": []})
        await asyncio.sleep(0)
        assert events[-2][1]["code"] == "replayed_action"
        assert events[-1][1]["code"] == "missing_scope"

        await client._execute_action(
            {
                "actionId": "remote-3",
                "type": "update_client",
                "scopes": ["update:install"],
            }
        )
        await asyncio.sleep(0)
        assert events[-1][1]["state"] == "rejected"
        assert events[-1][1]["code"] == "ota_disabled"

    asyncio.run(_scenario())


@pytest.mark.parametrize(
    ("exception", "expected_code"),
    [
        (HardwareCommandCancelled("stopped"), "cancelled_by_stop"),
        (OSError("adapter write failed"), "hardware_error"),
    ],
)
def test_hardware_worker_propagates_cancellation_and_adapter_failures(
    exception: Exception, expected_code: str
) -> None:
    async def _scenario() -> None:
        client = _PipelineCommands()
        client._hardware_lock = asyncio.Lock()

        def execute(*_args: object) -> None:
            raise exception

        client.handler.execute = execute
        await client._run_hardware_command(
            "forward",
            1,
            {"actionId": "command-1"},
            motion_command_id=client._latest_motion_command_id,
        )
        assert client.results[-1] == ("rejected", expected_code)

    asyncio.run(_scenario())


def test_tts_commands_emit_one_acceptance_and_exact_final_outcomes() -> None:
    async def _scenario() -> None:
        client = _TTSPipelineCommands()
        client.config = RobotConfig(
            server=ServerConfig(claim_token="claim-token"),
            tts=TTSConfig(enabled=True, type="espeak"),
        )
        client.tts = _LoopTTSStub()
        client._running = True
        now = time.time() * 1000

        client._process_command(
            "tts:say", "hello", now, "test", {"actionId": "tts-ok", "sender": "viewer"}
        )
        client._process_command(
            "tts:say",
            "https://example.com",
            now,
            "test",
            {"actionId": "tts-filtered", "sender": "viewer"},
        )
        client._process_command(
            "tts:say",
            "provider-error",
            now,
            "test",
            {"actionId": "tts-failed", "sender": "viewer"},
        )

        task = asyncio.create_task(client._tts_loop())
        await asyncio.wait_for(client._tts_queue.join(), timeout=2)
        client._running = False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert client.results == [
            ("accepted", "accepted"),
            ("accepted", "accepted"),
            ("accepted", "accepted"),
            ("completed", "tts_played"),
            ("rejected", "tts_url_blocked"),
            ("rejected", "tts_failed"),
        ]

    asyncio.run(_scenario())


def test_tts_queue_overflow_is_rejected_without_false_acceptance() -> None:
    client = _TTSPipelineCommands()
    client.tts = _LoopTTSStub()
    now = time.time() * 1000
    for index in range(client._tts_queue.maxsize + 1):
        client._process_command(
            "tts:say",
            f"message-{index}",
            now,
            "test",
            {"actionId": f"tts-{index}", "sender": "viewer"},
        )

    assert client.results.count(("accepted", "accepted")) == client._tts_queue.maxsize
    assert client.results[-1] == ("rejected", "tts_queue_full")


def test_tts_control_commands_and_payload_normalization_have_stable_outcomes() -> None:
    client = _TTSPipelineCommands()
    client.tts = _LoopTTSStub()

    assert client._maybe_handle_tts_command("tts:mute") == (True, "completed")
    assert client._maybe_handle_tts_command("tts:unmute") == (True, "completed")
    assert client._maybe_handle_tts_command("tts:volume", "75") == (True, "completed")
    assert client._maybe_handle_tts_command("tts:volume", "not-a-volume") == (
        True,
        "invalid_tts_volume",
    )
    assert client._maybe_handle_tts_command("tts:say", "") == (True, "tts_empty")
    assert client._maybe_handle_tts_command("unknown") == (False, "not_tts")

    message, metadata = client._normalize_tts_payload(
        "tts:say",
        {"text": " hello ", "userId": "viewer-1", "nested": {"safe": True}},
    )
    assert message == "hello"
    assert metadata is not None
    assert metadata["userId"] == "viewer-1"
    assert client._normalize_tts_payload("say:embedded", None) == ("embedded", None)
    assert client._coerce_tts_volume({"level": 101}) == 100
    assert client._coerce_tts_volume({"volume": 0}) == 0


def test_all_remote_action_branches_report_exact_lifecycle(monkeypatch) -> None:
    async def scenario() -> None:
        client = _ActionPipelineCommands()
        client.config = RobotConfig(
            server=ServerConfig(claim_token="claim-token"),
            diagnostics=DiagnosticsConfig(upload_enabled=True),
        )
        client._processed_remote_action_ids = deque(maxlen=64)
        client._update_manager = object()
        client._update_in_progress = False
        client._active_update_action_id = None
        client._livekit_connected = True
        audio_tasks: list[asyncio.Task[None]] = []

        class AudioManager:
            audio_task = None

            def restart_audio(self, _room, _running_fn):
                task = asyncio.create_task(asyncio.sleep(10))
                audio_tasks.append(task)
                return task

        client._camera_runtimes = [
            SimpleNamespace(
                include_audio=True,
                video_profile=SimpleNamespace(has_audio=lambda: True),
                manager=AudioManager(),
            )
        ]
        client._room = SimpleNamespace()
        client._running = True
        client._diag_enabled_until = 0.0
        client._capability_manifest = {}

        restarted: list[tuple[str, str | None]] = []
        frames = 0

        async def restart(reason: str, camera_id: str | None = None) -> None:
            nonlocal frames
            restarted.append((reason, camera_id))
            frames += 1

        client._total_camera_frames = lambda: frames  # type: ignore[method-assign]

        async def stop(_reason: str) -> bool:
            return True

        updates: list[str | None] = []

        async def update(action_id: str | None = None) -> None:
            updates.append(action_id)

        client._restart_camera_pipeline = restart  # type: ignore[method-assign]
        client._trigger_hardware_stop = stop  # type: ignore[method-assign]
        client._perform_client_update = update  # type: ignore[method-assign]
        client.handler = SimpleNamespace(close=lambda: None, reset_stop=lambda: None)
        replacement = SimpleNamespace(reset_stop=lambda: None)
        monkeypatch.setattr(
            "botparty_robot.remote_actions.create_hardware", lambda _config: replacement
        )
        monkeypatch.setattr(
            "botparty_robot.remote_actions.build_capability_manifest", lambda *_args: {"ok": True}
        )
        new_tts = _LoopTTSStub()
        monkeypatch.setattr(
            "botparty_robot.remote_actions.create_tts_profile", lambda _config: new_tts
        )

        actions = [
            ("restart_video", "media:restart"),
            ("restart_control", "control:restart"),
            ("reset_safety", "safety:reset"),
            ("restart_tts", "speak:restart"),
            ("restart_audio", "media:restart"),
            ("set_log_stream", "diagnostics:read"),
            ("update_client", "update:install"),
        ]
        for index, (action_type, scope) in enumerate(actions):
            await client._execute_action(
                {
                    "actionId": f"branch-{index}",
                    "type": action_type,
                    "scopes": [scope],
                    "durationSec": 15,
                }
            )
        await asyncio.sleep(0)

        assert restarted == [("remote action restart_video", None)]
        assert client.handler is replacement
        assert client.tts is new_tts
        assert client._capability_manifest == {"ok": True}
        assert client._diag_enabled_until > time.time()
        assert updates == ["branch-6"]
        assert client.remote_results.count(("accepted", "accepted")) == len(actions)
        assert client.remote_results.count(("completed", "completed")) == len(actions) - 1
        for task in audio_tasks:
            task.cancel()

    asyncio.run(scenario())


def test_remote_action_failure_and_diagnostics_policy_are_not_false_successes() -> None:
    async def scenario() -> None:
        client = _ActionPipelineCommands()
        client._processed_remote_action_ids = deque(maxlen=8)
        client._livekit_connected = True
        client._camera_runtimes = []
        client._room = None
        client._running = True
        client._update_manager = object()
        client._update_in_progress = True
        client.config = RobotConfig(server=ServerConfig(claim_token="claim-token"))

        async def fail_restart(*_args: object) -> None:
            raise RuntimeError("camera failed")

        client._restart_camera_pipeline = fail_restart  # type: ignore[method-assign]
        await client._execute_action(
            {
                "actionId": "failed-video",
                "type": "restart_video",
                "scopes": ["media:restart"],
            }
        )
        await client._execute_action(
            {
                "actionId": "disabled-diagnostics",
                "type": "set_log_stream",
                "scopes": ["diagnostics:read"],
            }
        )
        await client._execute_action(
            {
                "actionId": "concurrent-update",
                "type": "update_client",
                "scopes": ["update:install"],
            }
        )
        await client._execute_action({"type": "restart_tts", "scopes": ["speak:restart"]})

        assert ("failed", "action_failed") in client.remote_results
        assert ("rejected", "diagnostics_disabled") in client.remote_results
        assert ("rejected", "update_in_progress") in client.remote_results

    asyncio.run(scenario())


def test_remote_action_results_are_sent_as_bounded_protocol_events() -> None:
    async def scenario() -> None:
        events: list[tuple[str, dict[str, object]]] = []

        class Gateway:
            async def send_event(self, event: str, payload: dict[str, object]) -> bool:
                events.append((event, payload))
                return True

        client = _PipelineCommands()
        client._gateway = Gateway()
        client._emit_remote_action_result("action-1", "completed", "completed")
        await asyncio.sleep(0)
        assert events[0][1]["actionId"] == "action-1"
        assert events[0][1]["state"] == "completed"
        assert events[0][1]["code"] == "completed"

    asyncio.run(scenario())


def test_command_validation_rejects_every_unsafe_precondition() -> None:
    now = time.time() * 1000

    client = _PipelineCommands()
    client._process_command("forward", None, "invalid", "test", {"actionId": "bad-time"})
    assert client.results[-1] == ("rejected", "invalid_timestamp")

    client._process_command("forward", None, now + 6_000, "test", {"actionId": "future"})
    assert client.results[-1] == ("rejected", "stale_action")

    client = _PipelineCommands()
    client.handler.capabilities = lambda: HardwareCapabilities(
        commands=("forward",),
        motion_commands=("forward",),
        safe_stop=False,
        close=True,
        support_level="supported",
    )
    client._process_command("forward", None, now, "test", {"actionId": "unsafe-adapter"})
    assert client.results[-1] == ("rejected", "safe_stop_unverified")

    client = _PipelineCommands()
    client._safety.stop("test")
    client._process_command("forward", None, now, "test", {"actionId": "latched"})
    assert client.results[-1] == ("rejected", "safety_latched")

    client = _PipelineCommands()
    client.config.safety.require_media_for_motion = True
    client._media_operational = lambda: False  # type: ignore[method-assign]
    client._process_command("forward", None, now, "test", {"actionId": "no-media"})
    assert client.results[-1] == ("rejected", "media_not_ready")

    client = _PipelineCommands()
    client._process_command("not-supported", None, now, "test", {"actionId": "unknown"})
    assert client.results[-1] == ("rejected", "unsupported_command")


def test_chat_tts_routing_and_queue_cancellation_have_exact_results() -> None:
    client = _TTSPipelineCommands()
    client.tts = _LoopTTSStub()
    client.config.tts.chat_to_tts = True
    now = time.time() * 1000

    client._process_command(
        "chat",
        {"text": "hello", "userId": "viewer-1"},
        now,
        "test",
        {"actionId": "chat-spoken"},
    )
    queued = client._tts_queue.get_nowait()
    assert queued.message == "hello"
    assert queued.metadata == {
        "actionId": "chat-spoken",
        "text": "hello",
        "userId": "viewer-1",
        "sender": "viewer-1",
    }
    client._tts_queue.task_done()

    client._process_command("chat", ".silent", now, "test", {"actionId": "chat-hidden"})
    assert client.results[-1] == ("completed", "chat_ignored")

    client._tts_queue.put_nowait(QueuedTTSCommand("cancel me", {"actionId": "cancel-me"}))
    assert client._maybe_handle_tts_command("tts:mute") == (True, "completed")
    assert ("rejected", "tts_cancelled") in client.results

    client = _TTSPipelineCommands()
    client._process_command("chat", "hello", now, "test", {"actionId": "chat-only"})
    assert client.results[-1] == ("completed", "chat_received")


def test_hardware_worker_success_staleness_media_and_latch_paths() -> None:
    async def scenario() -> None:
        client = _PipelineCommands()
        client._hardware_lock = asyncio.Lock()
        executed: list[str] = []
        client.handler.execute = lambda _permit, command, *_args: executed.append(command)

        await client._run_hardware_command(
            "forward", 1, {"actionId": "success"}, client._latest_motion_command_id
        )
        assert executed == ["forward"]
        assert client.results[-1] == ("completed", "completed")

        await client._run_hardware_command("forward", 1, None, -1)
        assert executed == ["forward"]

        client.config.safety.require_media_for_motion = True
        client._media_operational = lambda: False  # type: ignore[method-assign]
        await client._run_hardware_command(
            "forward", 1, {"actionId": "worker-media"}, client._latest_motion_command_id
        )
        assert client.results[-1] == ("rejected", "media_not_ready")

        client.config.safety.require_media_for_motion = False
        client._safety.stop("test")
        await client._run_hardware_command(
            "forward", 1, {"actionId": "worker-latched"}, client._latest_motion_command_id
        )
        assert client.results[-1] == ("rejected", "safety_latched")

    asyncio.run(scenario())


def test_hardware_queue_is_bounded_and_worker_drains_commands() -> None:
    async def scenario() -> None:
        client = _PipelineCommands()
        client._hardware_lock = asyncio.Lock()
        client._running = True
        executed: list[str] = []
        client.handler.execute = lambda _permit, command, *_args: executed.append(command)
        for index in range(client._hardware_commands.capacity):
            assert client._enqueue_hardware_command(
                QueuedHardwareCommand("forward", index, None, None)
            )
        assert not client._enqueue_hardware_command(
            QueuedHardwareCommand("forward", 99, {"actionId": "full"}, None)
        )
        assert client.results[-1] == ("rejected", "command_queue_full")

        task = asyncio.create_task(client._hardware_command_loop())
        while client._hardware_commands:
            await asyncio.sleep(0)
        client._running = False
        client._hardware_commands.wake()
        await asyncio.wait_for(task, timeout=1)
        assert executed == ["forward"] * 4

    asyncio.run(scenario())


def test_stop_state_machine_reports_success_timeout_error_and_pending() -> None:
    async def scenario() -> None:
        client = _DummyCommands()
        client.stats.watchdog_stops = 0
        client.handler.reset_stop = lambda: None
        actions = RemoteActionExecutor(client, {})
        assert await client._trigger_hardware_stop("motion_deadline") is True
        assert client.stats.last_stop_status == "confirmed"
        assert client.stats.watchdog_stops == 1
        await actions._reset_hardware_stop()
        assert client._safety.snapshot().latched is False

        client = _DummyCommands()
        client.config.safety.stop_timeout_ms = 10
        client.handler.apply_emergency_stop = lambda: time.sleep(0.05)
        assert await client._trigger_hardware_stop("timeout-test") is False
        assert client.stats.last_stop_status == "timeout"
        actions = RemoteActionExecutor(client, {})
        with pytest.raises(RuntimeError, match="stop operation is pending"):
            await actions._reset_hardware_stop()
        await asyncio.sleep(0.06)
        with pytest.raises(RuntimeError, match="last stop was not confirmed"):
            await actions._reset_hardware_stop()

        client = _DummyCommands()
        client.handler.apply_emergency_stop = lambda: (_ for _ in ()).throw(OSError("stop failed"))
        assert await client._trigger_hardware_stop("error-test") is False
        assert client.stats.last_stop_error_code == "stop_adapter_error"

        client = _DummyCommands()
        pending = asyncio.create_task(asyncio.sleep(0.05))
        client._stop_worker_task = pending
        assert await client._trigger_hardware_stop("pending-test") is False
        pending.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pending

    asyncio.run(scenario())


def test_real_command_ack_event_has_terminal_protocol_state() -> None:
    async def scenario() -> None:
        events: list[tuple[str, dict[str, object]]] = []

        class Gateway:
            async def send_event(self, event: str, payload: dict[str, object]) -> bool:
                events.append((event, payload))
                return True

        client = _PipelineCommands()
        client._gateway = Gateway()
        client._record_product_milestone = lambda _name: None  # type: ignore[method-assign]
        ClientCommandsMixin._emit_command_result(
            client, {"actionId": "command-ack"}, "completed", "completed"
        )
        await asyncio.sleep(0)
        assert events[0][1] == {
            "commandId": "command-ack",
            "status": "ACK",
            "state": "completed",
            "message": None,
        }
        assert client.stats.last_command_ack_at > 0

    asyncio.run(scenario())
