import asyncio
import time
from types import SimpleNamespace
from typing import cast

import botparty_robot.client_ops as client_ops_module
from botparty_robot.client_ops import ClientOpsComponent, ClientOpsMixin, OperationsHost
from botparty_robot.client_state import WatchdogStats


class _DummyOps(ClientOpsMixin):
    pass


def test_legacy_git_updater_is_not_exposed():
    assert not hasattr(_DummyOps, "_build_git_pull_argv")
    assert not hasattr(_DummyOps, "_run_update_command")


class _SupervisorHost:
    def __init__(self) -> None:
        self._running = True
        self._health_task = None
        self._camera_runtimes: list[object] = []
        self._livekit_connected = False
        self._room = None
        self._tts_task = None
        self._heartbeat_task = None
        self._gateway_task = None
        self._gateway = SimpleNamespace(connected=True)
        self._update_manager = None
        self._ota_confirmed = False
        self._update_in_progress = False
        self._systemd_ready_sent = False
        self._last_heartbeat_stale_warning_at = 0.0
        self._supervisor_last_tick_monotonic = 0.0
        self.stats = WatchdogStats(last_heartbeat_at=time.time() - 120)
        self.milestones: list[str] = []
        self.outbox_drains = 0
        self.faults: list[tuple[str, str, bool, str]] = []
        self.restarts: list[tuple[str, str | None]] = []

    def _record_product_milestone(self, name: str) -> None:
        self.milestones.append(name)

    async def _drain_outcome_outbox(self) -> None:
        self.outbox_drains += 1

    def _total_camera_frames(self) -> int:
        return 0

    def _media_required(self) -> bool:
        return False

    def _media_operational(self) -> bool:
        return True

    def _build_health_snapshot(self) -> dict[str, object]:
        return {"ready": True, "degraded": []}

    def _record_fault(
        self,
        code: str,
        subsystem: str,
        *,
        retryable: bool,
        safe_detail: str = "",
    ) -> None:
        self.faults.append((code, subsystem, retryable, safe_detail))

    async def _restart_camera_pipeline(
        self,
        reason: str,
        camera_id: str | None = None,
    ) -> None:
        self.restarts.append((reason, camera_id))


def test_supervisor_keeps_watchdog_readiness_and_outbox_order(monkeypatch) -> None:
    async def scenario() -> None:
        host = _SupervisorHost()
        component = ClientOpsComponent(cast(OperationsHost, host))
        notifications: list[str] = []

        async def stop_after_first_tick(_delay: float) -> None:
            host._running = False

        monkeypatch.setattr(client_ops_module.asyncio, "sleep", stop_after_first_tick)
        monkeypatch.setattr(client_ops_module, "notify_systemd", notifications.append)

        await component._supervisor()

        assert notifications == ["WATCHDOG=1", "READY=1\nSTATUS=Ready"]
        assert host._systemd_ready_sent is True
        assert host.milestones == ["control_ready"]
        assert host.outbox_drains == 1
        assert host._last_heartbeat_stale_warning_at > 0

    asyncio.run(scenario())


def test_supervisor_restarts_failed_camera_and_records_fault(monkeypatch) -> None:
    async def scenario() -> None:
        host = _SupervisorHost()
        host._gateway.connected = False
        host._livekit_connected = True

        async def fail_camera() -> None:
            raise RuntimeError("camera failed")

        camera_task = asyncio.create_task(fail_camera())
        await asyncio.sleep(0)
        host._camera_runtimes = [
            SimpleNamespace(
                camera_id="front",
                task=camera_task,
                manager=SimpleNamespace(frame_count=0, audio_task=None),
                video_profile=SimpleNamespace(has_audio=lambda: False),
                include_audio=False,
                restart_count=0,
                started_at_monotonic=0.0,
                last_frame_at_monotonic=0.0,
                last_frame_count=0,
                state="starting",
                last_error=None,
            )
        ]
        component = ClientOpsComponent(cast(OperationsHost, host))

        async def stop_after_first_tick(_delay: float) -> None:
            host._running = False

        monkeypatch.setattr(client_ops_module.asyncio, "sleep", stop_after_first_tick)
        monkeypatch.setattr(client_ops_module, "notify_systemd", lambda _message: None)

        await component._supervisor()

        assert host.stats.camera_task_restarts == 1
        assert host.restarts == [("supervisor attempt 1/5", "front")]
        assert host.faults == [("camera_task_failed", "media", True, "RuntimeError")]
        assert host._camera_runtimes[0].last_error == "camera failed"

    asyncio.run(scenario())
