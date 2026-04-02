"""NavQ / MAVSDK rover adapter."""

from __future__ import annotations

import asyncio
from typing import Any

from .base import BaseHardware
from .common import optional_import


class HardwareAdapter(BaseHardware):
    profile_name = "navq"
    description = "MAVSDK offboard control adapter for NavQ robots"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.mavsdk = optional_import("mavsdk", "mavsdk")
        self.rover = self.mavsdk.System() if self.mavsdk else None
        self.yaw_step = self.option_float("yaw_step", 45.0)
        self.thrust = self.option_float("thrust", 0.1)
        self.system_address = self.option_str("system_address", "serial:///dev/ttymxc2:921600")
        self._ready = False

    def setup(self) -> None:
        if self.rover is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.log.warning("no running event loop available; NavQ connection deferred")
            return
        loop.create_task(self._connect())

    async def _connect(self) -> None:
        if self.rover is None:
            return
        try:
            await self.rover.connect(system_address=self.system_address)
            async for state in self.rover.core.connection_state():
                if state.is_connected:
                    break
            await self.rover.action.arm()
            await self.rover.offboard.set_attitude(self.mavsdk.offboard.Attitude(0.0, 0.0, 0.0, 0.0))
            await self.rover.offboard.start()
            self._ready = True
            self.log.info("connected on %s", self.system_address)
        except Exception as exc:
            self.log.warning("setup failed: %s", exc)

    async def _drive(self, yaw: float, thrust: float, duration: float) -> None:
        if self.rover is None or not self._ready:
            return
        attitude = self.mavsdk.offboard.Attitude(0.0, 0.0, yaw, thrust)
        await self.rover.offboard.set_attitude(attitude)
        if duration > 0:
            await asyncio.sleep(duration)
        await self.rover.offboard.set_attitude(self.mavsdk.offboard.Attitude(0.0, 0.0, yaw, 0.0))

    def on_command(self, command: str, value: Any = None) -> None:
        if self.rover is None:
            self.log.info("command=%s value=%s", command, value)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.log.warning("no running event loop available for NavQ command")
            return
        if self.matches(command, "forward"):
            loop.create_task(self._drive(0.0, self.thrust, 1.0))
        elif self.matches(command, "backward"):
            loop.create_task(self._drive(0.0, -self.thrust, 1.0))
        elif self.matches(command, "left"):
            loop.create_task(self._drive(-self.yaw_step, self.thrust, 1.0))
        elif self.matches(command, "right"):
            loop.create_task(self._drive(self.yaw_step, self.thrust, 1.0))
        elif self.matches(command, "stop"):
            loop.create_task(self._drive(0.0, 0.0, 0.0))

    def emergency_stop(self) -> None:
        if self.rover is not None:
            try:
                asyncio.get_running_loop().create_task(self._drive(0.0, 0.0, 0.0))
            except RuntimeError:
                self.log.warning("no running event loop available for NavQ emergency_stop")
