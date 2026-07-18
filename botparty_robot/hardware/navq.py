"""NavQ / MAVSDK rover adapter."""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Coroutine
from typing import Any

from ..config import RobotConfig
from ..safety import CommandPermit, HardwareCommandCancelled
from .base import BaseHardware
from .common import optional_import


class HardwareAdapter(BaseHardware):
    supported_commands = ("forward", "backward", "left", "right", "stop")
    motion_commands = supported_commands[:-1]
    profile_name = "navq"
    description = "MAVSDK offboard control adapter for NavQ robots"

    def __init__(self, config: RobotConfig) -> None:
        super().__init__(config)
        self.mavsdk = optional_import("mavsdk", "mavsdk")
        self.rover = self.mavsdk.System() if self.mavsdk else None
        self.yaw_step = self.option_float("yaw_step", 45.0)
        self.thrust = self.option_float("thrust", 0.1)
        self.system_address = self.option_str("system_address", "serial:///dev/ttymxc2:921600")
        self._ready = False
        self._state = "disconnected"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connect_task: asyncio.Task[None] | None = None

    def setup(self) -> None:
        if self.rover is None:
            return
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self.log.warning("no running event loop available; NavQ connection deferred")
            return
        self._connect_task = self._loop.create_task(self._connect())

    async def _connect(self) -> None:
        if self.rover is None:
            return
        try:
            await self.rover.connect(system_address=self.system_address)
            async for state in self.rover.core.connection_state():
                if state.is_connected:
                    break
            self._ready = True
            self._state = "connected_disarmed"
            self.log.info("connected and disarmed on %s", self.system_address)
        except asyncio.CancelledError:
            self._state = "disconnected"
            raise
        except Exception as exc:
            self._state = "failed"
            self.log.warning("setup failed: %s", exc)

    async def _drive(
        self,
        yaw: float,
        thrust: float,
        duration: float,
        permit: CommandPermit | None = None,
    ) -> None:
        if self.rover is None or not self._ready:
            return
        if permit is not None:
            permit.ensure_active()
        attitude = self.mavsdk.offboard.Attitude(0.0, 0.0, yaw, thrust)
        await self.rover.offboard.set_attitude(attitude)
        deadline = asyncio.get_running_loop().time() + duration
        while duration > 0 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(min(0.05, deadline - asyncio.get_running_loop().time()))
            if permit is not None:
                permit.ensure_active()
        await self.rover.offboard.set_attitude(self.mavsdk.offboard.Attitude(0.0, 0.0, yaw, 0.0))

    def _schedule(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Schedule a coroutine on the stored event loop, safe to call from any thread."""
        if self._loop is None:
            self.log.warning("no event loop stored; NavQ command dropped")
            return
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)

        def _consume_result(done: concurrent.futures.Future[Any]) -> None:
            try:
                done.result()
            except HardwareCommandCancelled:
                return
            except Exception as exc:
                self.log.warning("scheduled NavQ operation failed: %s", exc)

        future.add_done_callback(_consume_result)

    def on_command(self, command: str, value: Any = None) -> None:
        if self.rover is None:
            self.log.info("command=%s value=%s", command, value)
            return
        permit = self.current_command_permit()
        if self.matches(command, "forward"):
            self._schedule(self._drive(0.0, self.thrust, 1.0, permit))
        elif self.matches(command, "backward"):
            self._schedule(self._drive(0.0, -self.thrust, 1.0, permit))
        elif self.matches(command, "left"):
            self._schedule(self._drive(-self.yaw_step, self.thrust, 1.0, permit))
        elif self.matches(command, "right"):
            self._schedule(self._drive(self.yaw_step, self.thrust, 1.0, permit))
        elif self.matches(command, "stop"):
            self.emergency_stop()

    def emergency_stop(self) -> None:
        if self.rover is None or self._loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._stop_and_disarm(), self._loop)
        try:
            future.result(timeout=self.safety.stop_timeout_ms / 1000.0)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise TimeoutError("NavQ stop/disarm was not confirmed before its deadline") from exc

    async def _stop_and_disarm(self) -> None:
        if self.rover is None:
            return
        if self._state == "armed":
            await self.rover.offboard.set_attitude(
                self.mavsdk.offboard.Attitude(0.0, 0.0, 0.0, 0.0)
            )
            await self.rover.offboard.stop()
        await self.rover.action.disarm()
        self._ready = False
        self._state = "connected_disarmed"

    def _close_resources(self) -> None:
        if (
            self._connect_task is not None
            and not self._connect_task.done()
            and self._loop is not None
        ):
            self._loop.call_soon_threadsafe(self._connect_task.cancel)
        self._ready = False
        self._state = "closed"
