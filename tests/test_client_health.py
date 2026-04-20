import asyncio
import contextlib
import socket
from types import SimpleNamespace

import aiohttp

from botparty_robot.client_runtime import ClientLifecycleMixin
from botparty_robot.client_state import WatchdogStats


class _DummyClient(ClientLifecycleMixin):
    def __init__(self, port: int) -> None:
        self._running = True
        self._robot_id = "robot-1"
        self._livekit_connected = True
        self._gateway = SimpleNamespace(connected=True)
        self._http_session = None
        self.stats = WatchdogStats(commands_received=4, reconnect_attempts=2, camera_task_restarts=1)
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
            )
        ]

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
            assert payload["status"] == "ok"
            assert payload["gatewayConnected"] is True
            assert payload["livekitConnected"] is True
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

    asyncio.run(_scenario())