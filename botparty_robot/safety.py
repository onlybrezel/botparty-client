"""Thread-safe safety state shared by the asyncio client and hardware workers."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

_T = TypeVar("_T")


class SafetyLatchedError(RuntimeError):
    """Raised when actuator work is attempted while the safety latch is active."""


class HardwareCommandCancelled(RuntimeError):
    """Raised inside a hardware worker after its command epoch was invalidated."""


@dataclass(frozen=True, slots=True)
class SafetySnapshot:
    epoch: int
    latched: bool
    reason: str | None
    stopped_at_monotonic: float | None


class CommandPermit:
    """Capability token for exactly one safety epoch."""

    def __init__(
        self,
        controller: SafetyController,
        epoch: int,
        cancelled: threading.Event,
    ) -> None:
        self._controller = controller
        self.epoch = epoch
        self._cancelled = cancelled

    def ensure_active(self) -> None:
        if self._cancelled.is_set() or not self._controller.is_epoch_active(self.epoch):
            raise HardwareCommandCancelled("hardware command was cancelled by the safety latch")

    def wait(self, seconds: float) -> None:
        """Wait for a pulse duration while remaining immediately interruptible."""

        self.ensure_active()
        if self._cancelled.wait(max(0.0, seconds)):
            raise HardwareCommandCancelled("hardware command was cancelled during a timed phase")
        self.ensure_active()

    def run_guarded(self, operation: Callable[[], _T]) -> _T:
        """Run a write without holding the global latch lock across device I/O."""

        return self._controller.run_if_epoch_active(self.epoch, operation)


class SafetyController:
    """Owns the stop latch and invalidates all work from older command epochs."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._epoch = 0
        self._latched = False
        self._reason: str | None = None
        self._stopped_at_monotonic: float | None = None
        self._cancelled = threading.Event()

    def snapshot(self) -> SafetySnapshot:
        with self._lock:
            return SafetySnapshot(
                epoch=self._epoch,
                latched=self._latched,
                reason=self._reason,
                stopped_at_monotonic=self._stopped_at_monotonic,
            )

    def issue_permit(self) -> CommandPermit:
        with self._lock:
            if self._latched:
                raise SafetyLatchedError(
                    f"hardware safety latch is active ({self._reason or 'unspecified'})"
                )
            return CommandPermit(self, self._epoch, self._cancelled)

    def stop(self, reason: str) -> SafetySnapshot:
        """Latch the controller and cancel every permit issued before this call."""

        with self._lock:
            self._cancelled.set()
            self._epoch += 1
            self._latched = True
            self._reason = reason
            self._stopped_at_monotonic = time.monotonic()
            return self.snapshot()

    def reset(self) -> SafetySnapshot:
        """Create a fresh epoch after an explicitly authorized reset."""

        with self._lock:
            self._epoch += 1
            self._cancelled = threading.Event()
            self._latched = False
            self._reason = None
            self._stopped_at_monotonic = None
            return self.snapshot()

    def is_epoch_active(self, epoch: int) -> bool:
        with self._lock:
            return not self._latched and self._epoch == epoch

    def run_if_epoch_active(self, epoch: int, operation: Callable[[], _T]) -> _T:
        """Reject stale work while keeping ``stop`` independent from blocking I/O.

        Adapters that cannot cancel an already-started device write must not
        advertise confirmed safe-stop capability. The second epoch check makes
        a late completion observable to the command pipeline.
        """

        with self._lock:
            if self._latched or self._epoch != epoch:
                raise HardwareCommandCancelled(
                    "hardware command was cancelled before an actuator write"
                )
        result = operation()
        if not self.is_epoch_active(epoch):
            raise HardwareCommandCancelled(
                "hardware command completed after the safety latch changed"
            )
        return result
