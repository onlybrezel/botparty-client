"""Control WebSocket connection to the BotParty gateway."""

import asyncio
import contextlib
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from .config import RobotConfig
from .protocol import MAX_WEBSOCKET_MESSAGE_BYTES, ControlCommand
from .ws_protocol import WS_EVENTS, WS_PROTOCOL_VERSION

logger = logging.getLogger("botparty.gateway")


class GatewayConnection:
    """Maintains a persistent WebSocket control channel to the BotParty gateway.

    Reconnects automatically with exponential backoff. The caller provides
    three callbacks:
      on_command(command, value, timestamp, metadata) - called for each control:command event
      on_emergency_stop()                   - called for control:emergency-stop
      on_actions(data) -> coroutine         - called for robot:actions events (async)
    """

    def __init__(
        self,
        config: RobotConfig,
        on_command: Callable[[str, Any, Any, dict[str, Any] | None], None],
        on_emergency_stop: Callable[[], None],
        on_actions: Callable[[dict[str, Any]], Awaitable[None]],
        running_fn: Callable[[], bool],
        session_provider: Callable[[], aiohttp.ClientSession] | None = None,
        on_shutdown: Callable[[str, str, float, str], Awaitable[None]] | None = None,
        on_reconnected: Callable[[str, str], Awaitable[None]] | None = None,
        on_disconnected: Callable[[str], Awaitable[None]] | None = None,
        on_reconnect_attempt: Callable[[], None] | None = None,
    ) -> None:
        self.config = config
        self._on_command = on_command
        self._on_emergency_stop = on_emergency_stop
        self._on_actions = on_actions
        self._on_shutdown = on_shutdown
        self._on_reconnected = on_reconnected
        self._on_disconnected = on_disconnected
        self._on_reconnect_attempt = on_reconnect_attempt
        self._running_fn = running_fn
        self._session_provider = session_provider
        self._connected = False
        self._ws: aiohttp.ClientWebSocketResponse[bool] | None = None
        self._last_actions_pull_at = 0.0
        self._retry_after_override_sec: float | None = None
        self._shutdown_reason: str | None = None
        self._shutdown_message: str | None = None
        self._shutdown_scope: str | None = None
        self._pending_recovery_reason: str | None = None
        self._pending_recovery_scope: str | None = None
        self._disconnect_notified = False
        self._has_attempted_connection = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def close(self) -> None:
        """Close the active WebSocket connection immediately (for clean shutdown or restart)."""
        ws = self._ws
        self._connected = False
        self._ws = None
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()

    async def send_event(self, event: str, data: dict[str, Any]) -> bool:
        """Send an event over the active WebSocket. Returns False if not connected."""
        ws = self._ws
        if not self._connected or ws is None:
            return False
        try:
            await ws.send_json({"event": event, "data": data})
            return True
        except Exception:
            return False

    async def run(self) -> None:
        """WebSocket loop - reconnects with exponential backoff."""
        ws_url = self._resolve_ws_url()
        attempt = 0

        while self._running_fn():
            attempt += 1
            reconnect_callback = self._on_reconnect_attempt
            if self._has_attempted_connection and reconnect_callback is not None:
                reconnect_callback()
            self._has_attempted_connection = True
            connected_this_attempt = False
            reconnect_delay = min(2 ** min(attempt, 6), 30) + random.uniform(0, 2)
            try:
                timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_read=30)
                session_provider = self._session_provider
                owns_session = session_provider is None
                session = (
                    aiohttp.ClientSession(timeout=timeout)
                    if session_provider is None
                    else session_provider()
                )
                try:
                    async with session.ws_connect(
                        ws_url,
                        heartbeat=20,
                        max_msg_size=MAX_WEBSOCKET_MESSAGE_BYTES,
                    ) as ws:
                        self._ws = ws
                        attempt = 0
                        self._connected = False
                        self._retry_after_override_sec = None
                        self._shutdown_reason = None
                        self._shutdown_message = None
                        self._shutdown_scope = None
                        robot_auth_token = (
                            self.config.server.robot_auth_token_value() or ""
                        ).strip()
                        if not robot_auth_token:
                            raise RuntimeError("Missing robot auth token for websocket claim")
                        await ws.send_json(
                            {
                                "event": WS_EVENTS["ROBOT_CLAIM"],
                                "data": {
                                    "robotAuthToken": robot_auth_token,
                                    "protocolVersion": WS_PROTOCOL_VERSION,
                                },
                            }
                        )
                        await self._await_robot_claim(ws)
                        self._connected = True
                        connected_this_attempt = True
                        self._disconnect_notified = False
                        logger.info("Control websocket connected")
                        await self._pull_actions(ws, force=True)
                        if self._pending_recovery_reason and self._on_reconnected is not None:
                            try:
                                await self._on_reconnected(
                                    self._pending_recovery_reason,
                                    self._pending_recovery_scope or "app",
                                )
                            except Exception as exc:
                                logger.warning("Reconnect callback failed: %s", exc)
                            finally:
                                self._pending_recovery_reason = None
                                self._pending_recovery_scope = None
                                self._disconnect_notified = False

                        while self._running_fn():
                            try:
                                msg = await ws.receive(timeout=10)
                            except asyncio.TimeoutError:
                                await ws.send_json(
                                    {"event": WS_EVENTS["ROBOT_HEARTBEAT"], "data": {}}
                                )
                                await self._pull_actions(ws)
                                continue

                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await self._handle_message(msg.data)
                            elif msg.type in (
                                aiohttp.WSMsgType.CLOSED,
                                aiohttp.WSMsgType.CLOSING,
                                aiohttp.WSMsgType.ERROR,
                            ):
                                logger.warning(
                                    "Control websocket closed by gateway "
                                    "(type=%s code=%s error=%s)",
                                    msg.type.name,
                                    ws.close_code,
                                    ws.exception(),
                                )
                                break
                finally:
                    if owns_session and not session.closed:
                        with contextlib.suppress(Exception):
                            await session.close()
                reconnect_delay = self._consume_reconnect_delay(default_delay=1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reconnect_delay = self._consume_reconnect_delay(default_delay=reconnect_delay)
                logger.warning(
                    "Control websocket disconnected (%s); retrying in %ds",
                    exc,
                    int(reconnect_delay),
                )
            finally:
                self._ws = None
                self._connected = False
                should_notify = connected_this_attempt or self._pending_recovery_reason is not None
                if (
                    self._running_fn()
                    and should_notify
                    and not self._disconnect_notified
                    and self._on_disconnected is not None
                ):
                    try:
                        await self._on_disconnected(self._pending_recovery_scope or "app")
                    except Exception as exc:
                        logger.warning("Disconnect callback failed: %s", exc)
                    finally:
                        self._disconnect_notified = True

            if self._running_fn():
                self._log_reconnect_delay(reconnect_delay)
                await asyncio.sleep(reconnect_delay)

    async def _await_robot_claim(self, ws: aiohttp.ClientWebSocketResponse[bool]) -> None:
        while self._running_fn():
            try:
                msg = await ws.receive(timeout=10)
            except asyncio.TimeoutError as exc:
                raise RuntimeError("Timed out waiting for robot claim acknowledgement") from exc

            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    payload = json.loads(msg.data)
                except Exception:
                    continue

                event = payload.get("event")
                data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                if event == WS_EVENTS["ROBOT_CLAIM"]:
                    if data.get("success") is True:
                        return
                    raise RuntimeError(str(data.get("message") or "Robot claim failed"))

                if event == WS_EVENTS["ERROR"]:
                    raise RuntimeError(str(data.get("message") or "Gateway rejected robot claim"))

                await self._handle_message(msg.data)
                continue

            if msg.type in (
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.ERROR,
            ):
                raise RuntimeError(
                    "Gateway closed the websocket during robot claim "
                    f"(type={msg.type.name} code={ws.close_code} error={ws.exception()})"
                )

        raise RuntimeError("Robot client stopped before websocket claim completed")

    async def _handle_message(self, raw: str) -> None:
        if len(raw.encode("utf-8", errors="replace")) > MAX_WEBSOCKET_MESSAGE_BYTES:
            logger.warning("Rejected websocket message larger than 64 KiB")
            return
        try:
            payload = json.loads(raw)
        except Exception:
            return

        event = payload.get("event")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

        if event == WS_EVENTS["CONTROL_COMMAND"]:
            try:
                command = ControlCommand.model_validate(data)
            except Exception as exc:
                logger.warning("Rejected invalid control command: %s", exc)
                return
            metadata = dict(command.metadata or {})
            metadata["actionId"] = command.command_id
            metadata["commandId"] = command.command_id
            metadata["ackRequired"] = command.ack_required
            if command.button_id:
                metadata["buttonId"] = command.button_id
            if command.user_id:
                metadata["userId"] = command.user_id
            self._on_command(
                command.command,
                command.value,
                command.timestamp,
                metadata or None,
            )
        elif event == WS_EVENTS["CONTROL_EMERGENCY_STOP"]:
            logger.warning("Emergency stop received from gateway")
            self._on_emergency_stop()
        elif event == WS_EVENTS["ROBOT_ACTIONS"]:
            await self._on_actions(data)
        elif event == WS_EVENTS["ERROR"]:
            message = str(data.get("message") or "Gateway error")
            logger.warning("Gateway error event: %s", message)
        elif event == WS_EVENTS["SERVER_SHUTDOWN"]:
            retry_after_ms = data.get("retryAfterMs")
            try:
                if not isinstance(retry_after_ms, (str, int, float)):
                    raise TypeError
                retry_after_sec = max(1.0, float(retry_after_ms) / 1000.0)
            except (TypeError, ValueError):
                retry_after_sec = 12.0

            reason = str(data.get("reason") or "restart")
            scope = str(data.get("scope") or "app").strip().lower() or "app"
            message = str(data.get("message") or "Server restart announced. Reconnecting soon...")
            self._retry_after_override_sec = retry_after_sec
            self._shutdown_reason = reason
            self._shutdown_message = message
            self._shutdown_scope = scope
            self._pending_recovery_reason = reason
            self._pending_recovery_scope = scope
            logger.warning(
                "Gateway announced %s (%s); reconnecting in %.0fs - %s",
                reason,
                scope,
                retry_after_sec,
                message,
            )
            if self._on_shutdown is not None:
                try:
                    await self._on_shutdown(reason, message, retry_after_sec, scope)
                except Exception as exc:
                    logger.warning("Shutdown callback failed: %s", exc)

    async def _pull_actions(
        self,
        ws: aiohttp.ClientWebSocketResponse[bool],
        force: bool = False,
    ) -> None:
        now = time.time()
        if not force and now - self._last_actions_pull_at < 2.5:
            return
        await ws.send_json({"event": WS_EVENTS["ROBOT_ACTIONS_PULL"], "data": {}})
        self._last_actions_pull_at = now

    def _resolve_ws_url(self) -> str:
        api_url = self.config.server.api_url.rstrip("/")
        if api_url.endswith("/api/v1"):
            api_url = api_url[:-7]
        if api_url.startswith("https://"):
            return f"wss://{api_url[len('https://') :]}/ws"
        if api_url.startswith("http://"):
            return f"ws://{api_url[len('http://') :]}/ws"
        return f"ws://{api_url}/ws"

    def _consume_reconnect_delay(self, default_delay: float) -> float:
        delay = self._retry_after_override_sec
        self._retry_after_override_sec = None
        return delay if delay is not None else default_delay

    def _log_reconnect_delay(self, delay: float) -> None:
        if self._shutdown_reason or self._shutdown_message:
            logger.warning(
                "Control websocket reconnect scheduled in %.0fs (%s)",
                delay,
                self._shutdown_reason or self._shutdown_scope or "restart",
            )
            self._shutdown_reason = None
            self._shutdown_message = None
            self._shutdown_scope = None
            return

        logger.warning("Control websocket disconnected; retrying in %.0fs", delay)
