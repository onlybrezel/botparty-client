import asyncio
import json
from types import SimpleNamespace

import aiohttp

from botparty_robot.config import RobotConfig, ServerConfig
from botparty_robot.gateway import GatewayConnection


async def _async_noop(_payload: dict) -> None:
    return None


def _build_config(api_url: str = "https://botparty.live/api/v1") -> RobotConfig:
    return RobotConfig(
        server=ServerConfig(
            api_url=api_url,
            claim_token="claim-token",
            robot_auth_token="robot-token",
        )
    )


def _build_gateway(**kwargs: object) -> GatewayConnection:
    config = kwargs.pop("config", _build_config())
    return GatewayConnection(
        config,
        on_command=kwargs.pop("on_command", lambda *_args: None),
        on_emergency_stop=kwargs.pop("on_emergency_stop", lambda: None),
        on_actions=kwargs.pop("on_actions", _async_noop),
        running_fn=kwargs.pop("running_fn", lambda: False),
        session_provider=kwargs.pop("session_provider", None),
        on_shutdown=kwargs.pop("on_shutdown", None),
        on_reconnected=kwargs.pop("on_reconnected", None),
        on_disconnected=kwargs.pop("on_disconnected", None),
    )


def test_resolve_ws_url_strips_api_prefix_and_uses_wss() -> None:
    gateway = _build_gateway(config=_build_config("https://example.com/api/v1"))
    assert gateway._resolve_ws_url() == "wss://example.com/ws"


def test_send_event_returns_false_when_disconnected() -> None:
    gateway = _build_gateway()
    assert asyncio.run(gateway.send_event("robot:heartbeat", {})) is False


def test_handle_shutdown_message_updates_reconnect_state() -> None:
    gateway = _build_gateway(running_fn=lambda: True)

    asyncio.run(
        gateway._handle_message(
            json.dumps(
                {
                    "event": "server:shutdown",
                    "data": {
                        "retryAfterMs": 7000,
                        "reason": "update",
                        "scope": "app",
                        "message": "Deploy in progress",
                    },
                }
            )
        )
    )

    assert gateway._retry_after_override_sec == 7.0
    assert gateway._pending_recovery_reason == "update"
    assert gateway._pending_recovery_scope == "app"


def test_handle_command_message_invokes_callback() -> None:
    calls: list[tuple[str, object, object, dict[str, object] | None]] = []
    gateway = _build_gateway(
        on_command=lambda command, value, timestamp, metadata: calls.append(
            (command, value, timestamp, metadata)
        )
    )

    asyncio.run(
        gateway._handle_message(
            json.dumps(
                {
                    "event": "control:command",
                    "data": {
                        "command": "forward",
                        "value": 1,
                        "timestamp": 123,
                        "metadata": {"source": "test"},
                    },
                }
            )
        )
    )

    assert calls == [("forward", 1, 123, {"source": "test"})]


def test_handle_emergency_stop_message_invokes_callback() -> None:
    calls: list[str] = []
    gateway = _build_gateway(on_emergency_stop=lambda: calls.append("estop"))

    asyncio.run(
        gateway._handle_message(
            json.dumps(
                {
                    "event": "control:emergency-stop",
                    "data": {},
                }
            )
        )
    )

    assert calls == ["estop"]


def test_gateway_reuses_shared_session_provider_across_reconnects() -> None:
    class _FakeWsContext:
        def __init__(self) -> None:
            self._received = False

        async def __aenter__(self) -> "_FakeWsContext":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def send_json(self, payload: dict[str, object]) -> None:
            del payload

        async def receive(self, timeout: float | None = None) -> SimpleNamespace:
            del timeout
            if not self._received:
                self._received = True
                return SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=None)
            return SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=None)

    class _FakeSession:
        def __init__(self) -> None:
            self.ws_connect_calls = 0
            self.closed = False

        def ws_connect(self, url: str, heartbeat: int = 20) -> _FakeWsContext:
            del url, heartbeat
            self.ws_connect_calls += 1
            return _FakeWsContext()

    session = _FakeSession()
    gateway = _build_gateway(
        running_fn=lambda: session.ws_connect_calls < 2,
        session_provider=lambda: session,  # type: ignore[arg-type]
    )
    gateway._consume_reconnect_delay = lambda default_delay: 0.0  # type: ignore[method-assign]
    gateway._log_reconnect_delay = lambda delay: None  # type: ignore[method-assign]

    asyncio.run(gateway.run())

    assert session.ws_connect_calls == 2


def test_gateway_run_notifies_disconnect_and_reconnect_callbacks_after_shutdown() -> None:
    class _FakeWsContext:
        def __init__(self, messages: list[SimpleNamespace]) -> None:
            self._messages = list(messages)

        async def __aenter__(self) -> "_FakeWsContext":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def send_json(self, payload: dict[str, object]) -> None:
            del payload

        async def receive(self, timeout: float | None = None) -> SimpleNamespace:
            del timeout
            if self._messages:
                return self._messages.pop(0)
            return SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=None)

    class _FakeSession:
        def __init__(self) -> None:
            self.ws_connect_calls = 0
            self.closed = False
            shutdown_payload = json.dumps(
                {
                    "event": "server:shutdown",
                    "data": {
                        "retryAfterMs": 1000,
                        "reason": "update",
                        "scope": "app",
                        "message": "Rolling restart",
                    },
                }
            )
            self._contexts = [
                _FakeWsContext(
                    [
                        SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=shutdown_payload),
                        SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=None),
                    ]
                ),
                _FakeWsContext([SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=None)]),
            ]

        def ws_connect(self, url: str, heartbeat: int = 20) -> _FakeWsContext:
            del url, heartbeat
            context = self._contexts[self.ws_connect_calls]
            self.ws_connect_calls += 1
            return context

    shutdown_calls: list[tuple[str, str, float, str]] = []
    disconnected_calls: list[str] = []
    reconnected_calls: list[tuple[str, str]] = []
    session = _FakeSession()
    gateway = _build_gateway(
        running_fn=lambda: session.ws_connect_calls < 2,
        session_provider=lambda: session,  # type: ignore[arg-type]
        on_shutdown=lambda reason, message, retry_after_sec, scope: shutdown_calls.append(
            (reason, message, retry_after_sec, scope)
        ) or _async_noop({}),
        on_disconnected=lambda scope: disconnected_calls.append(scope) or _async_noop({}),
        on_reconnected=lambda reason, scope: reconnected_calls.append((reason, scope)) or _async_noop({}),
    )
    gateway._consume_reconnect_delay = lambda default_delay: 0.0  # type: ignore[method-assign]
    gateway._log_reconnect_delay = lambda delay: None  # type: ignore[method-assign]

    asyncio.run(gateway.run())

    assert shutdown_calls == [("update", "Rolling restart", 1.0, "app")]
    assert disconnected_calls == ["app"]
    assert reconnected_calls == [("update", "app")]
