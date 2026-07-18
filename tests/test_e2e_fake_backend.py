from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from types import SimpleNamespace
from typing import Any

import aiohttp
from aiohttp import web
from pydantic import SecretStr

import botparty_robot.client_runtime as runtime_module
from botparty_robot.auth import AuthResult, ClientAuthenticator
from botparty_robot.client import BotPartyClient
from botparty_robot.config import RobotConfig, ServerConfig, VideoConfig
from botparty_robot.gateway import GatewayConnection


async def _serve(app: web.Application) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    server = site._server
    assert server is not None
    port = server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


def test_claim_gateway_command_estop_action_and_shutdown_end_to_end() -> None:
    async def scenario() -> None:
        received: list[dict[str, Any]] = []
        command_event = asyncio.Event()
        stop_event = asyncio.Event()
        action_event = asyncio.Event()
        running = True

        async def claim(request: web.Request) -> web.Response:
            payload = await request.json()
            received.append({"kind": "claim", "payload": payload})
            return web.json_response(
                {
                    "protocolVersion": 1,
                    "token": "fake-livekit-token",
                    "robotId": "robot-e2e",
                    "livekitUrl": "ws://127.0.0.1:7880",
                    "robotAuthToken": "fake-robot-auth",
                    "publishTokens": {"front": "fake-camera-token"},
                    "stream": {"targetBitrateKbps": 600},
                }
            )

        async def websocket(request: web.Request) -> web.WebSocketResponse:
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            claim_message = await ws.receive_json()
            received.append({"kind": "ws_claim", "payload": claim_message})
            await ws.send_json({"event": "robot:claim", "data": {"success": True}})
            pull = await ws.receive_json()
            received.append({"kind": "actions_pull", "payload": pull})
            await ws.send_json(
                {
                    "event": "control:command",
                    "data": {
                        "commandId": "29fa15ec-54a1-4a2b-9562-5fef04b507be",
                        "command": "light_on",
                        "timestamp": 123,
                        "ackRequired": True,
                    },
                }
            )
            await ws.send_json({"event": "control:emergency-stop", "data": {}})
            await ws.send_json(
                {
                    "event": "robot:actions",
                    "data": {
                        "actions": [
                            {
                                "actionId": "action-e2e",
                                "type": "restart_tts",
                                "scopes": ["speak:restart"],
                            }
                        ]
                    },
                }
            )
            await action_event.wait()
            await ws.close()
            return ws

        app = web.Application()
        app.router.add_post("/api/v1/robots/claim", claim)
        app.router.add_get("/ws", websocket)
        runner, api_url = await _serve(app)
        try:
            config = RobotConfig(
                server=ServerConfig(
                    api_url=api_url,
                    livekit_url="ws://127.0.0.1:7880",
                    claim_token="fake-claim-token",  # secret-scan: allow-test-fixture
                    device_key="d" * 64,
                    allow_insecure_dev_transport=True,
                )
            )
            async with aiohttp.ClientSession() as session:
                result = await ClientAuthenticator(config, lambda: session).claim(
                    publish_camera_ids=["front"], capabilities={"hash": "capability-e2e"}
                )
                assert isinstance(result, AuthResult)
                assert result.robot_id == "robot-e2e"
                assert result.target_bitrate_kbps == 600
                config.server.robot_auth_token = SecretStr(result.robot_auth_token)

                commands: list[tuple[str, Any, Any, dict[str, Any] | None]] = []

                def on_command(
                    command: str,
                    value: Any,
                    timestamp: Any,
                    metadata: dict[str, Any] | None,
                ) -> None:
                    commands.append((command, value, timestamp, metadata))
                    command_event.set()

                def on_stop() -> None:
                    stop_event.set()

                async def on_actions(data: dict[str, Any]) -> None:
                    nonlocal running
                    received.append({"kind": "actions", "payload": data})
                    action_event.set()
                    running = False

                gateway = GatewayConnection(
                    config,
                    on_command=on_command,
                    on_emergency_stop=on_stop,
                    on_actions=on_actions,
                    running_fn=lambda: running,
                    session_provider=lambda: session,
                )
                await asyncio.wait_for(gateway.run(), timeout=3)
                await asyncio.wait_for(command_event.wait(), timeout=1)
                await asyncio.wait_for(stop_event.wait(), timeout=1)

            assert received[0]["payload"]["claimToken"] == "fake-claim-token"
            assert received[1]["payload"]["data"]["robotAuthToken"] == "fake-robot-auth"
            assert received[2]["payload"]["event"] == "robot:actions:pull"
            assert commands[0][0] == "light_on"
            assert commands[0][3] is not None
            assert commands[0][3]["ackRequired"] is True
            assert received[-1]["payload"]["actions"][0]["actionId"] == "action-e2e"
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_complete_client_process_acks_reconnects_degrades_and_shuts_down(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        websocket_attempts = 0
        reconnect_seen = asyncio.Event()
        protocol_seen = asyncio.Event()
        finish = asyncio.Event()
        outbound: list[dict[str, Any]] = []

        class FakeRoom:
            def __init__(self) -> None:
                self.callbacks: dict[str, Any] = {}
                self.local_participant = SimpleNamespace()

            def on(self, event: str):
                def register(callback):
                    self.callbacks[event] = callback
                    return callback

                return register

            async def connect(self, _url: str, _token: str) -> None:
                return None

            async def disconnect(self) -> None:
                callback = self.callbacks.get("disconnected")
                if callback is not None:
                    callback()

        async def claim(_request: web.Request) -> web.Response:
            return web.json_response(
                {
                    "protocolVersion": 1,
                    "token": "process-livekit-token",
                    "robotId": "robot-process-e2e",
                    "livekitUrl": "ws://127.0.0.1:7880",
                    "robotAuthToken": "process-robot-auth",
                }
            )

        async def actions_poll(_request: web.Request) -> web.Response:
            return web.json_response({"actions": []})

        async def heartbeat(_request: web.Request) -> web.Response:
            return web.json_response({"ok": True})

        async def websocket(request: web.Request) -> web.WebSocketResponse:
            nonlocal websocket_attempts
            websocket_attempts += 1
            attempt = websocket_attempts
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            assert (await ws.receive_json())["event"] == "robot:claim"
            await ws.send_json({"event": "robot:claim", "data": {"success": True}})
            assert (await ws.receive_json())["event"] == "robot:actions:pull"

            if attempt == 1:
                await ws.send_json(
                    {
                        "event": "control:command",
                        "data": {
                            "commandId": "command-process-e2e",
                            "command": "chat",
                            "value": "hello",
                            "timestamp": time.time() * 1000,
                            "ackRequired": True,
                        },
                    }
                )
                await ws.send_json({"event": "control:emergency-stop", "data": {}})
                await ws.send_json(
                    {
                        "event": "robot:actions",
                        "data": {
                            "actions": [
                                {
                                    "actionId": "action-process-e2e",
                                    "type": "restart_tts",
                                    "scopes": ["speak:restart"],
                                }
                            ]
                        },
                    }
                )
                while True:
                    message = await asyncio.wait_for(ws.receive_json(), timeout=3)
                    outbound.append(message)
                    terminal_ack = any(
                        item.get("event") == "control:ack"
                        and item.get("data", {}).get("commandId") == "command-process-e2e"
                        for item in outbound
                    )
                    completed_action = any(
                        item.get("event") == "robot:action-result"
                        and item.get("data", {}).get("actionId") == "action-process-e2e"
                        and item.get("data", {}).get("state") == "completed"
                        for item in outbound
                    )
                    if terminal_ack and completed_action:
                        protocol_seen.set()
                        break
                await ws.close()
                return ws

            reconnect_seen.set()
            await finish.wait()
            await ws.close()
            return ws

        app = web.Application()
        app.router.add_post("/api/v1/robots/claim", claim)
        app.router.add_post("/api/v1/robots/actions/poll", actions_poll)
        app.router.add_post("/api/v1/robots/heartbeat", heartbeat)
        app.router.add_get("/ws", websocket)
        runner, api_url = await _serve(app)
        monkeypatch.setattr(runtime_module.rtc, "Room", FakeRoom)
        monkeypatch.setenv("BOTPARTY_HEALTH_ENABLED", "false")

        client = BotPartyClient(
            RobotConfig(
                server=ServerConfig(
                    api_url=api_url,
                    livekit_url="ws://127.0.0.1:7880",
                    claim_token="process-claim-token",  # secret-scan: allow-test-fixture
                    device_key="e" * 64,
                    allow_insecure_dev_transport=True,
                ),
                video=VideoConfig(type="none"),
            )
        )
        task = asyncio.create_task(client.run())
        try:
            await asyncio.wait_for(protocol_seen.wait(), timeout=5)
            assert client._safety.snapshot().latched is True
            assert any(
                item["event"] == "control:ack"
                and item["data"]
                == {
                    "commandId": "command-process-e2e",
                    "status": "ACK",
                    "state": "completed",
                    "message": "chat_received",
                }
                for item in outbound
            )
            assert any(
                item["event"] == "robot:action-result" and item["data"]["state"] == "accepted"
                for item in outbound
            )
            assert any(
                item["event"] == "robot:action-result" and item["data"]["state"] == "completed"
                for item in outbound
            )

            runtime = client._camera_runtimes[0]
            runtime.video_profile.capture_mode = lambda: "sdk"
            if runtime.task is not None:
                runtime.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await runtime.task
            snapshot = client._build_health_snapshot()
            assert snapshot["ready"] is False
            assert "media" in snapshot["degraded"]

            await asyncio.wait_for(reconnect_seen.wait(), timeout=5)
        finally:
            finish.set()
            await client.shutdown()
            await asyncio.wait_for(task, timeout=5)
            logging.getLogger("botparty").removeHandler(client._diag_handler)
            await runner.cleanup()

        assert websocket_attempts >= 2
        assert client._running is False
        assert client._http_session is not None and client._http_session.closed

    asyncio.run(scenario())
