"""Bounded latest-wins hardware command queue with explicit ownership."""

from __future__ import annotations

import asyncio
from collections import deque

from .client_state import QueuedHardwareCommand


class HardwareCommandQueue:
    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("hardware command queue capacity must be positive")
        self._pending: deque[QueuedHardwareCommand] = deque(maxlen=capacity)
        self._available = asyncio.Event()
        self.high_watermark = 0
        self.dropped = 0

    @property
    def capacity(self) -> int:
        assert self._pending.maxlen is not None
        return self._pending.maxlen

    def __len__(self) -> int:
        return len(self._pending)

    def pending(self) -> tuple[QueuedHardwareCommand, ...]:
        return tuple(self._pending)

    def offer(
        self, command: QueuedHardwareCommand
    ) -> tuple[bool, tuple[QueuedHardwareCommand, ...]]:
        superseded: tuple[QueuedHardwareCommand, ...] = ()
        if command.motion_command_id is not None:
            superseded = tuple(item for item in self._pending if item.motion_command_id is not None)
            if superseded:
                self._pending = deque(
                    (item for item in self._pending if item.motion_command_id is None),
                    maxlen=self.capacity,
                )
                self.dropped += len(superseded)
        if len(self._pending) >= self.capacity:
            self.dropped += 1
            return False, superseded
        self._pending.append(command)
        self.high_watermark = max(self.high_watermark, len(self._pending))
        self._available.set()
        return True, superseded

    def cancel_motion(self) -> tuple[QueuedHardwareCommand, ...]:
        cancelled = tuple(item for item in self._pending if item.motion_command_id is not None)
        if cancelled:
            self._pending = deque(
                (item for item in self._pending if item.motion_command_id is None),
                maxlen=self.capacity,
            )
            self.dropped += len(cancelled)
        return cancelled

    def pop_nowait(self) -> QueuedHardwareCommand | None:
        if not self._pending:
            self._available.clear()
            return None
        item = self._pending.popleft()
        if not self._pending:
            self._available.clear()
        return item

    async def wait(self) -> None:
        await self._available.wait()

    def wake(self) -> None:
        self._available.set()
