"""Control WebSocket connection to the BotParty gateway."""

import asyncio
import json
import logging
import time
from typing import Any, Callable, Coroutine, Optional

import aiohttp

from .config import RobotConfig

logger = logging.getLogger("botparty.gateway")


class GatewayConnection:
    """Maintains a persistent WebSocket control channel to the BotParty gateway.

    Reconnects automatically with exponential backoff. The caller provides
    three callbacks:
      on_command(command, value, timestamp) - called for each control:command event
      on_emergency_stop()                   - called for control:emergency-stop
      on_actions(data) -> coroutine         - called for robot:actions events (async)
    """

    def __init__(
        self,
        config: RobotConfig,
        on_command: Callable[[str, Any, Any], None],
        on_emergency_stop: Callable[[], None],
        on_actions: Callable[[dict], Coroutine],
        running_fn: Callable[[], bool],
    ) -> None:
        self.config = config
        self._on_command = on_command
        self._on_emergency_stop = on_emergency_stop
        self._on_actions = on_actions
        self._running_fn = running_fn
        self._connected = False
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._last_actions_pull_at = 0.0

    @property
    def connected(self) -> bool:
        return self._connected

    async def send_event(self, event: str, data: dict) -> bool:
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
            try:
                timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_read=30)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.ws_connect(ws_url, heartbeat=20) as ws:
                        self._ws = ws
                        attempt = 0
                        self._connected = True
                        logger.info("Control websocket connected")
                        await ws.send_json({
                            "event": "robot:claim",
                            "data": {"claimToken": self.config.server.claim_token},
                        })
                        await self._pull_actions(ws, force=True)

                        while self._running_fn():
                            try:
                                msg = await ws.receive(timeout=10)
                            except asyncio.TimeoutError:
                                await ws.send_json({"event": "robot:heartbeat", "data": {}})
                                await self._pull_actions(ws)
                                continue

                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await self._handle_message(msg.data)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except Exception as e:
                self._connected = False
                delay = min(2 ** min(attempt, 6), 30)
                logger.warning("Control websocket disconnected (%s); retrying in %ds", e, delay)
                await asyncio.sleep(delay)
            finally:
                self._ws = None
                self._connected = False

    async def _handle_message(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except Exception:
            return

        event = payload.get("event")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

        if event == "control:command":
            self._on_command(
                str(data.get("command", "")),
                data.get("value"),
                data.get("timestamp"),
            )
        elif event == "control:emergency-stop":
            logger.warning("Emergency stop received from gateway")
            self._on_emergency_stop()
        elif event == "robot:actions":
            await self._on_actions(data)

    async def _pull_actions(self, ws: aiohttp.ClientWebSocketResponse, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_actions_pull_at < 2.5:
            return
        await ws.send_json({"event": "robot:actions:pull", "data": {}})
        self._last_actions_pull_at = now

    def _resolve_ws_url(self) -> str:
        api_url = self.config.server.api_url.rstrip("/")
        if api_url.endswith("/api/v1"):
            api_url = api_url[:-7]
        if api_url.startswith("https://"):
            return f"wss://{api_url[len('https://'):]}/ws"
        if api_url.startswith("http://"):
            return f"ws://{api_url[len('http://'):]}/ws"
        return f"ws://{api_url}/ws"
