import asyncio
import contextlib
import os
import socket
from types import SimpleNamespace

import aiohttp

from botparty_robot.client_runtime import ClientLifecycleMixin
from botparty_robot.client_state import WatchdogStats
from botparty_robot.config import RobotConfig, ServerConfig
from botparty_robot.safety import SafetyController


class _DummyClient(ClientLifecycleMixin):
    def __init__(self, port: int) -> None:
        self._running = True
        self._robot_id = "robot-1"
        self._livekit_connected = True
        self._gateway = SimpleNamespace(connected=True)
        self.config = RobotConfig(
            server=ServerConfig(
                claim_token="claim-token",
                robot_auth_token="robot-auth-token",
            )
        )
        self._safety = SafetyController()
        self._watchdog_task = asyncio.create_task(asyncio.sleep(3600))
        self._http_session = None
        self._command_queue = []
        self._update_in_progress = False
        self._capability_manifest = {"schemaVersion": 1}
        self._client_git_commit = "abc123"
        self._client_git_branch = "test"
        self._client_git_dirty = False
        self.stats = WatchdogStats(
            commands_received=4, reconnect_attempts=2, camera_task_restarts=1
        )
        self._camera_runtimes = [
            SimpleNamespace(
                camera_id="front",
                label="Front",
                role="primary",
                publish_mode="always_on",
                task=asyncio.create_task(asyncio.sleep(3600)),
                restart_count=1,
                manager=SimpleNamespace(frame_count=12),
                include_audio=False,
                video_profile=SimpleNamespace(capture_mode=lambda: "sdk"),
                started_at_monotonic=0.0,
                last_frame_at_monotonic=asyncio.get_running_loop().time(),
            )
        ]

    def _get_safety_controller(self) -> SafetyController:
        return self._safety

    def _health_enabled(self) -> bool:
        return True

    def _health_host(self) -> str:
        return "127.0.0.1"

    def _health_port(self) -> int:
        return self._port

    def _get_uptime_sec(self) -> int:
        return 42

    def _total_camera_frames(self) -> int:
        return 12


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_local_health_endpoint_reports_runtime_status() -> None:
    async def _scenario() -> None:
        port = _reserve_port()
        client = _DummyClient(port)
        client._port = port
        task = asyncio.create_task(client._run_health_server())

        try:
            deadline = asyncio.get_running_loop().time() + 5.0
            payload: dict[str, object] | None = None
            async with aiohttp.ClientSession() as session:
                while True:
                    try:
                        async with session.get(f"http://127.0.0.1:{port}/health") as response:
                            payload = await response.json()
                        break
                    except aiohttp.ClientError:
                        if asyncio.get_running_loop().time() >= deadline:
                            raise
                        await asyncio.sleep(0.05)

            assert payload is not None
            assert payload["status"] == "ready"
            assert payload["ready"] is True
            assert payload["gatewayConnected"] is True
            assert payload["livekitConnected"] is True
            assert payload["build"]["id"].startswith("version-")
            assert payload["build"]["commit"] == "abc123"
            assert payload["activeCameras"] == 1
            assert payload["stats"]["commandsReceived"] == 4
        finally:
            client._running = False
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            for runtime in client._camera_runtimes:
                runtime.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await runtime.task
            client._watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await client._watchdog_task

    asyncio.run(_scenario())


def test_non_loopback_health_requires_private_token_file(tmp_path, monkeypatch) -> None:
    class _HealthAuth(ClientLifecycleMixin):
        def _health_host(self) -> str:
            return "0.0.0.0"

    token_file = tmp_path / "health.token"
    token_file.write_text("health-secret\n", encoding="utf-8")
    request = SimpleNamespace(headers={"Authorization": "Bearer health-secret"})
    client = _HealthAuth()
    monkeypatch.setenv("BOTPARTY_HEALTH_AUTH_TOKEN_FILE", str(token_file))

    os.chmod(token_file, 0o644)
    assert client._health_authorized(request) is False
    os.chmod(token_file, 0o600)
    assert client._health_authorized(request) is True
    request.headers["Authorization"] = "Bearer wrong"
    assert client._health_authorized(request) is False


def test_readiness_distinguishes_auth_control_media_safety_and_update() -> None:
    async def _scenario() -> None:
        client = _DummyClient(_reserve_port())
        try:
            assert client._build_health_snapshot()["ready"] is True

            client._gateway.connected = False
            snapshot = client._build_health_snapshot()
            assert snapshot["ready"] is False
            assert "control" in snapshot["degraded"]

            client._gateway.connected = True
            client._robot_id = ""
            snapshot = client._build_health_snapshot()
            assert snapshot["ready"] is False
            assert "auth" in snapshot["degraded"]

            client._robot_id = "robot-1"
            client._safety.stop("test")
            snapshot = client._build_health_snapshot()
            assert snapshot["ready"] is False
            assert "safety_latched" in snapshot["degraded"]

            client._safety.reset()
            client._update_in_progress = True
            snapshot = client._build_health_snapshot()
            assert snapshot["ready"] is False
            assert "update" in snapshot["degraded"]

            client._update_in_progress = False
            client._camera_runtimes[0].task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await client._camera_runtimes[0].task
            snapshot = client._build_health_snapshot()
            assert snapshot["ready"] is False
            assert "media" in snapshot["degraded"]

            client._camera_runtimes = []
            assert client._build_health_snapshot()["ready"] is True
        finally:
            for runtime in client._camera_runtimes:
                if not runtime.task.done():
                    runtime.task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await runtime.task
            client._watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await client._watchdog_task

    asyncio.run(_scenario())
