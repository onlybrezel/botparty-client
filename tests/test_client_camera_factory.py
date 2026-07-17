from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import botparty_robot.client_media as client_media_module
from botparty_robot.client_media import ClientMediaMixin
from botparty_robot.config import (
    CameraStreamConfig,
    CameraVideoOverrideConfig,
    RobotConfig,
    ServerConfig,
)


class _DummyClient(ClientMediaMixin):
    def __init__(self) -> None:
        self.config = SimpleNamespace()


class _DummyRecoveryClient(ClientMediaMixin):
    def __init__(self) -> None:
        self.config = SimpleNamespace()
        self._camera_restart_lock = asyncio.Lock()
        self._camera_runtimes: list[SimpleNamespace] = []
        self._primary_camera_id = "front"
        self._livekit_connected = True
        self._room = object()
        self._running = True
        self._recovery_restart_task: asyncio.Task[None] | None = None
        self._livekit_reconnect_task: asyncio.Task[None] | None = None
        self._room_reconnect_in_progress = False
        self._planned_reconnect_at = 0.0
        self._planned_reconnect_reason: str | None = None
        self._gateway_outage_started_at = 0.0
        self._gateway_outage_scope: str | None = None
        self._livekit_disconnected_during_gateway_outage = False

    def _uses_external_media_transport(self) -> bool:
        return True


def test_build_initial_camera_runtime_state_prefers_primary_runtime() -> None:
    client = _DummyClient()
    secondary_runtime = SimpleNamespace(
        camera_id="rear",
        role="secondary",
        manager="rear-manager",
        video_profile="rear-profile",
    )
    primary_runtime = SimpleNamespace(
        camera_id="front",
        role="primary",
        manager="front-manager",
        video_profile="front-profile",
    )
    client._build_camera_runtimes = lambda: [secondary_runtime, primary_runtime]  # type: ignore[method-assign]

    runtimes, primary_camera_id = client._build_initial_camera_runtime_state()

    assert runtimes == [secondary_runtime, primary_runtime]
    assert primary_camera_id == "front"


def test_build_initial_camera_runtime_state_handles_empty_registry() -> None:
    client = _DummyClient()
    client._build_camera_runtimes = lambda: []  # type: ignore[method-assign]
    runtimes, primary_camera_id = client._build_initial_camera_runtime_state()

    assert runtimes == []
    assert primary_camera_id == "front"


def test_restart_camera_pipeline_skips_when_media_transport_is_not_ready(monkeypatch) -> None:
    async def _scenario() -> None:
        client = _DummyRecoveryClient()
        client._livekit_connected = False
        client._camera_runtimes = [
            SimpleNamespace(
                camera_id="front",
                config=SimpleNamespace(),
                video_profile="old-profile",
                manager=SimpleNamespace(video_profile="old-profile"),
                task=None,
            )
        ]
        cancelled: list[str] = []
        client._cancel_camera_task = lambda runtime: cancelled.append(runtime.camera_id)  # type: ignore[method-assign]
        monkeypatch.setattr(
            client_media_module, "create_video_profile", lambda config: "new-profile"
        )

        await client._restart_camera_pipeline("shutdown")

        assert cancelled == []
        assert client._camera_runtimes[0].video_profile == "old-profile"

    asyncio.run(_scenario())


def test_restart_camera_pipeline_restarts_only_the_targeted_camera(monkeypatch) -> None:
    async def _scenario() -> None:
        client = _DummyRecoveryClient()
        front_runtime = SimpleNamespace(
            camera_id="front",
            config=SimpleNamespace(name="front-config"),
            video_profile="front-old",
            manager=SimpleNamespace(video_profile="front-old"),
            task=None,
        )
        rear_runtime = SimpleNamespace(
            camera_id="rear",
            config=SimpleNamespace(name="rear-config"),
            video_profile="rear-old",
            manager=SimpleNamespace(video_profile="rear-old"),
            task=None,
        )
        client._camera_runtimes = [front_runtime, rear_runtime]
        cancelled: list[str] = []
        started: list[str] = []

        async def _cancel(runtime: SimpleNamespace) -> None:
            cancelled.append(runtime.camera_id)

        async def _start_camera(runtime: SimpleNamespace) -> None:
            started.append(runtime.camera_id)

        client._cancel_camera_task = _cancel  # type: ignore[method-assign]
        client._start_camera = _start_camera  # type: ignore[method-assign]

        created_profiles = iter(["front-new"])
        monkeypatch.setattr(
            client_media_module,
            "create_video_profile",
            lambda config: next(created_profiles),
        )

        await client._restart_camera_pipeline("manual", camera_id="front")
        await asyncio.gather(
            *(runtime.task for runtime in client._camera_runtimes if runtime.task),
            return_exceptions=True,
        )

        assert cancelled == ["front"]
        assert started == ["front"]
        assert front_runtime.video_profile == "front-new"
        assert front_runtime.manager.video_profile == "front-new"
        assert rear_runtime.video_profile == "rear-old"
        assert rear_runtime.manager.video_profile == "rear-old"

    asyncio.run(_scenario())


def test_cancel_timeout_keeps_publisher_owner_reference() -> None:
    async def _scenario() -> None:
        client = _DummyRecoveryClient()
        release = asyncio.Event()

        async def stubborn() -> None:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                await release.wait()

        task = asyncio.create_task(stubborn())
        runtime = SimpleNamespace(task=task, state="streaming", last_error=None)
        await asyncio.sleep(0)
        await client._cancel_camera_task(runtime, timeout_sec=0.01)
        assert runtime.task is task
        assert runtime.state == "failed"

        release.set()
        await task

    asyncio.run(_scenario())


def test_handle_gateway_reconnected_schedules_direct_publish_recovery_after_long_outage() -> None:
    async def _scenario() -> None:
        client = _DummyRecoveryClient()
        client._gateway_outage_started_at = time.time() - (
            client_media_module.GATEWAY_RECOVERY_RESTART_THRESHOLD_SEC + 1
        )
        client._gateway_outage_scope = "app"
        recovered: list[str] = []

        async def _recover(reason: str) -> None:
            recovered.append(reason)

        client._recover_direct_publish_after_gateway_reconnect = _recover  # type: ignore[method-assign]

        await client._handle_gateway_reconnected("update", "app")
        assert client._recovery_restart_task is not None
        await client._recovery_restart_task

        assert recovered == ["update"]

    asyncio.run(_scenario())


def test_audio_source_skips_disabled_and_non_audio_cameras(monkeypatch) -> None:
    class _Profile:
        def __init__(self, config) -> None:
            self._audio = bool(config.video.options.get("audio_capable"))

        def has_audio(self) -> bool:
            return self._audio

        def publish_transport(self) -> str:
            return "livekit"

    client = _DummyClient()
    client.config = RobotConfig(
        server=ServerConfig(claim_token="claim-token"),
        cameras=[
            CameraStreamConfig(
                id="disabled",
                enabled=False,
                video=CameraVideoOverrideConfig(options={"audio_capable": True}),
            ),
            CameraStreamConfig(
                id="silent",
                video=CameraVideoOverrideConfig(options={"audio_capable": False}),
            ),
            CameraStreamConfig(
                id="rear",
                video=CameraVideoOverrideConfig(options={"audio_capable": True}),
            ),
            CameraStreamConfig(
                id="side",
                video=CameraVideoOverrideConfig(options={"audio_capable": True}),
            ),
        ],
    )
    client._livekit_publish_tokens = {}
    client._livekit_publish_token = ""
    monkeypatch.setattr(client_media_module, "create_video_profile", _Profile)

    runtimes = client._build_camera_runtimes()
    assert [runtime.camera_id for runtime in runtimes if runtime.include_audio] == ["rear"]

    client.config.audio_source_camera_id = "side"
    runtimes = client._build_camera_runtimes()
    assert [runtime.camera_id for runtime in runtimes if runtime.include_audio] == ["side"]


def test_server_bitrate_is_a_hard_cap() -> None:
    client = _DummyClient()
    assert client._resolve_target_bitrate_kbps(remote=900, configured=1_500, default=2_200) == 900
    assert (
        client._resolve_target_bitrate_kbps(remote=2_000, configured=1_200, default=2_200) == 1_200
    )


def test_gateway_disconnect_stops_before_recording_outage() -> None:
    async def _scenario() -> None:
        client = _DummyRecoveryClient()
        events: list[str] = []
        client.stats = SimpleNamespace(
            control_disconnects=0,
            last_control_disconnect_reason=None,
        )

        async def _stop(reason: str) -> None:
            assert client.stats.control_disconnects == 0
            events.append(reason)

        client._trigger_hardware_stop = _stop  # type: ignore[method-assign]
        await client._handle_gateway_disconnected("app")
        assert events == ["control_disconnected"]
        assert client.stats.control_disconnects == 1
        assert client._gateway_outage_scope == "app"

    asyncio.run(_scenario())
