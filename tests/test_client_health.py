import asyncio
import contextlib
import os
import socket
from types import SimpleNamespace

import aiohttp
import pytest

import botparty_robot.client_runtime as runtime_module
from botparty_robot.auth import AuthResult
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
        self._hardware_commands = []
        self._update_in_progress = False
        self._supervisor_last_tick_monotonic = asyncio.get_running_loop().time()
        self._health_started_event = asyncio.Event()
        self._health_started_event.set()
        self._health_start_error = None
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
                manager=SimpleNamespace(frame_count=12, video_track_published=True),
                include_audio=False,
                video_profile=SimpleNamespace(capture_mode=lambda: "sdk"),
                started_at_monotonic=0.0,
                last_frame_at_monotonic=asyncio.get_running_loop().time(),
                last_frame_count=0,
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
        client._health_started_event.clear()
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


def test_metrics_are_opt_in_and_have_no_high_cardinality_labels(monkeypatch) -> None:
    async def _scenario() -> None:
        client = _DummyClient(_reserve_port())
        request = SimpleNamespace(headers={}, path="/metrics")
        try:
            monkeypatch.setenv("BOTPARTY_METRICS_ENABLED", "true")
            client.stats.claim_latency_ms.extend([10.0, 20.0])
            client.stats.command_receive_latency_ms.extend([5.0, 15.0])
            client.stats.command_execution_ms.extend([25.0, 50.0])
            client.stats.stop_confirmation_ms.extend([30.0, 60.0])
            client.stats.control_reconnect_ms.extend([1_000.0, 2_000.0])
            client.stats.first_frame_ms.extend([300.0, 600.0])
            client.stats.media_restart_ms.extend([400.0, 800.0])
            assert client._metrics_enabled() is True
            response = await client._handle_metrics_request(request)
            text = response.text
            assert "botparty_ready 1" in text
            assert "botparty_safety_stops_total" in text
            assert "botparty_gateway_connected 1" in text
            assert "botparty_safety_stop_confirmed 1" in text
            assert "botparty_claim_latency_ms_p95 20.0" in text
            assert "botparty_command_execution_ms_p99 50.0" in text
            assert "botparty_stop_confirmation_ms_p99 60.0" in text
            assert "botparty_control_reconnect_ms_p95 2000.0" in text
            assert "botparty_first_frame_ms_p95 600.0" in text
            assert "botparty_media_restart_ms_p95 800.0" in text
            assert "robot-1" not in text
            assert "{" not in text
        finally:
            for runtime in client._camera_runtimes:
                runtime.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await runtime.task
            client._watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await client._watchdog_task

    asyncio.run(_scenario())


def test_health_environment_parsing_and_shared_session(monkeypatch) -> None:
    async def scenario() -> None:
        client = object.__new__(ClientLifecycleMixin)
        client._http_session = None
        monkeypatch.setenv("BOTPARTY_HEALTH_ENABLED", "off")
        monkeypatch.setenv("BOTPARTY_HEALTH_HOST", "")
        monkeypatch.setenv("BOTPARTY_HEALTH_PORT", "invalid")
        monkeypatch.setenv("BOTPARTY_METRICS_ENABLED", "yes")
        assert client._health_enabled() is False
        assert client._health_host() == "127.0.0.1"
        assert client._health_port() == 9100
        assert client._metrics_enabled() is True

        monkeypatch.setenv("BOTPARTY_HEALTH_PORT", "70000")
        assert client._health_port() == 9100
        monkeypatch.setenv("BOTPARTY_HEALTH_PORT", "9200")
        assert client._health_port() == 9200

        session = client._get_session()
        assert client._get_session() is session
        await session.close()
        replacement = client._get_session()
        assert replacement is not session
        await replacement.close()

    asyncio.run(scenario())


def test_health_token_file_rejects_missing_symlink_directory_and_empty(
    tmp_path, monkeypatch
) -> None:
    client = object.__new__(ClientLifecycleMixin)
    monkeypatch.delenv("BOTPARTY_HEALTH_AUTH_TOKEN_FILE", raising=False)
    assert client._health_auth_token() is None

    missing = tmp_path / "missing"
    monkeypatch.setenv("BOTPARTY_HEALTH_AUTH_TOKEN_FILE", str(missing))
    assert client._health_auth_token() is None

    directory = tmp_path / "directory"
    directory.mkdir()
    monkeypatch.setenv("BOTPARTY_HEALTH_AUTH_TOKEN_FILE", str(directory))
    assert client._health_auth_token() is None

    token = tmp_path / "token"
    token.write_text("", encoding="utf-8")
    os.chmod(token, 0o600)
    monkeypatch.setenv("BOTPARTY_HEALTH_AUTH_TOKEN_FILE", str(token))
    assert client._health_auth_token() is None

    link = tmp_path / "link"
    link.symlink_to(token)
    monkeypatch.setenv("BOTPARTY_HEALTH_AUTH_TOKEN_FILE", str(link))
    assert client._health_auth_token() is None


def test_health_and_metrics_handlers_enforce_auth_and_readiness() -> None:
    async def scenario() -> None:
        client = _DummyClient(_reserve_port())
        client._port = _reserve_port()
        request = SimpleNamespace(headers={}, path="/health")
        try:
            response = await client._handle_health_request(request)
            assert response.status == 200
            assert response.headers["Cache-Control"] == "no-store"

            client._gateway.connected = False
            request.path = "/ready"
            assert (await client._handle_health_request(request)).status == 503
            request.path = "/live"
            client._supervisor_last_tick_monotonic = 0
            assert (await client._handle_health_request(request)).status == 503

            client._health_authorized = lambda _request: False  # type: ignore[method-assign]
            assert (await client._handle_health_request(request)).status == 401
            assert (await client._handle_metrics_request(request)).status == 401
        finally:
            for runtime in client._camera_runtimes:
                runtime.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await runtime.task
            client._watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await client._watchdog_task

    asyncio.run(scenario())


def test_media_readiness_handles_on_demand_startup_and_stalls() -> None:
    async def scenario() -> None:
        client = _DummyClient(_reserve_port())
        try:
            runtime = client._camera_runtimes[0]
            now = asyncio.get_running_loop().time()
            runtime.manager.frame_count = 0
            assert client._media_operational() is False

            runtime.manager.video_track_published = False
            runtime.manager.frame_count = 1
            runtime.last_frame_at_monotonic = now
            assert client._media_operational() is False

            runtime.manager.video_track_published = True
            runtime.last_frame_at_monotonic = now - 16
            assert client._media_operational() is False

            runtime.last_frame_at_monotonic = now
            client._livekit_connected = False
            assert client._media_operational() is False

            client._camera_runtimes = [
                SimpleNamespace(
                    camera_id="rear",
                    publish_mode="on_demand",
                    video_profile=SimpleNamespace(capture_mode=lambda: "sdk"),
                )
            ]
            client._primary_camera_id = "front"
            assert client._media_required() is False
            assert client._media_operational() is True
        finally:
            runtime.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runtime.task
            client._watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await client._watchdog_task

    asyncio.run(scenario())


def test_health_server_disabled_and_bind_failure_are_explicit(monkeypatch) -> None:
    async def scenario() -> None:
        client = _DummyClient(_reserve_port())
        client._port = _reserve_port()
        faults: list[tuple[str, str]] = []
        client._runtime_faults = SimpleNamespace(
            record=lambda code, subsystem, **_kwargs: faults.append((code, subsystem))
        )
        try:
            client._health_enabled = lambda: False  # type: ignore[method-assign]
            await client._run_health_server()

            client._health_enabled = lambda: True  # type: ignore[method-assign]

            class Runner:
                def __init__(self, app, access_log=None) -> None:
                    del app, access_log

                async def setup(self) -> None:
                    return None

                async def cleanup(self) -> None:
                    return None

            class Site:
                def __init__(self, runner, host, port) -> None:
                    del runner, host, port

                async def start(self) -> None:
                    raise OSError("address in use")

            monkeypatch.setattr(runtime_module.web, "AppRunner", Runner)
            monkeypatch.setattr(runtime_module.web, "TCPSite", Site)
            with pytest.raises(RuntimeError, match="could not be started"):
                await client._run_health_server()
            assert client._health_start_error == "OSError"
            assert faults == [("health_bind_failed", "health")]
        finally:
            for runtime in client._camera_runtimes:
                runtime.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await runtime.task
            client._watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await client._watchdog_task

    asyncio.run(scenario())


def test_run_authenticates_and_selects_direct_transport(monkeypatch) -> None:
    async def scenario() -> None:
        client = _DummyClient(_reserve_port())
        client._port = _reserve_port()
        client._robot_id = ""
        client._livekit_publish_tokens = {}
        client._livekit_publish_token = ""
        client._health_started_event.set()
        outcomes = iter(
            [
                None,
                AuthResult(
                    token="publish-token",
                    robot_id="robot-2",
                    livekit_url="wss://media.botparty.live/rtc",
                    publish_tokens={"front": "front-token"},
                    robot_auth_token="robot-token",
                    target_bitrate_kbps=900,
                ),
            ]
        )

        async def authenticate():
            return next(outcomes)

        async def connect_direct() -> None:
            client._running = False

        async def no_sleep(_delay: float) -> None:
            return None

        client._authenticate = authenticate  # type: ignore[method-assign]
        client._uses_direct_livekit_publisher = lambda: True  # type: ignore[method-assign]
        client._connect_direct_livekit = connect_direct  # type: ignore[method-assign]
        client._ensure_background_tasks = lambda: None  # type: ignore[method-assign]
        client._record_product_milestone = lambda _name: None  # type: ignore[method-assign]
        monkeypatch.setattr(runtime_module.asyncio, "sleep", no_sleep)
        try:
            await client.run()
            assert client._robot_id == "robot-2"
            assert client._livekit_publish_token == "publish-token"
            assert client._livekit_publish_tokens == {"front": "front-token"}
            assert client.config.server.livekit_url == "wss://media.botparty.live"
        finally:
            for runtime in client._camera_runtimes:
                runtime.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await runtime.task
            client._watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await client._watchdog_task

    asyncio.run(scenario())


def test_direct_connection_attempts_and_reconnect_delay(monkeypatch) -> None:
    async def scenario() -> None:
        client = _DummyClient(_reserve_port())
        client._port = _reserve_port()
        client._media_connection_attempts = 0
        client._livekit_publish_token = ""

        async def no_sleep(_delay: float) -> None:
            return None

        monkeypatch.setattr(runtime_module.asyncio, "sleep", no_sleep)
        try:
            await client._connect_direct_livekit()
            assert client._media_connection_attempts == 1

            client._livekit_publish_token = "publish-token"

            async def start() -> None:
                client._running = False

            stopped: list[bool] = []

            async def stop() -> None:
                stopped.append(True)

            client._start_all_cameras = start  # type: ignore[method-assign]
            client._stop_media_tasks = stop  # type: ignore[method-assign]
            client._ensure_background_tasks = lambda: None  # type: ignore[method-assign]
            client._running = True
            await client._connect_direct_livekit()
            assert client.stats.media_reconnects == 1
            assert stopped == [True]

            client._planned_reconnect_at = 0
            client._planned_reconnect_reason = "none"
            assert client._consume_reconnect_delay(2) == 2
            client._planned_reconnect_at = runtime_module.time.time() + 5
            client._planned_reconnect_reason = "upgrade"
            assert client._consume_reconnect_delay(2) >= 4
            assert client._planned_reconnect_reason is None
        finally:
            for runtime in client._camera_runtimes:
                runtime.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await runtime.task
            client._watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await client._watchdog_task

    asyncio.run(scenario())


def test_background_task_registry_starts_each_singleton_once() -> None:
    async def scenario() -> None:
        client = object.__new__(ClientLifecycleMixin)
        names = (
            "_heartbeat_task",
            "_watchdog_task",
            "_actions_task",
            "_diag_upload_task",
            "_tts_task",
            "_gateway_task",
            "_command_task",
            "_health_task",
        )
        for name in names:
            setattr(client, name, None)

        blocker = asyncio.Event()

        async def wait_forever() -> None:
            await blocker.wait()

        client._heartbeat_loop = wait_forever  # type: ignore[method-assign]
        client._supervisor = wait_forever  # type: ignore[method-assign]
        client._actions_loop = wait_forever  # type: ignore[method-assign]
        client._diagnostics_upload_loop = wait_forever  # type: ignore[method-assign]
        client._tts_loop = wait_forever  # type: ignore[method-assign]
        client._hardware_command_loop = wait_forever  # type: ignore[method-assign]
        client._run_health_server = wait_forever  # type: ignore[method-assign]
        client._gateway = SimpleNamespace(run=wait_forever)

        client._ensure_background_tasks()
        first = [getattr(client, name) for name in names]
        client._ensure_background_tasks()
        assert [getattr(client, name) for name in names] == first
        for task in first:
            task.cancel()
        await asyncio.gather(*first, return_exceptions=True)

    asyncio.run(scenario())
