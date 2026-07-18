"""Bounded, privacy-safe runtime fault registry."""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import asdict, dataclass

from .redaction import redact_text

FAULT_CODE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


@dataclass(frozen=True, slots=True)
class RuntimeFault:
    code: str
    subsystem: str
    retryable: bool
    safe_detail: str
    occurred_at: int


class FaultRegistry:
    def __init__(self, capacity: int = 32) -> None:
        if not 1 <= capacity <= 256:
            raise ValueError("fault registry capacity must be between 1 and 256")
        self._faults: deque[RuntimeFault] = deque(maxlen=capacity)

    def record(
        self,
        code: str,
        subsystem: str,
        *,
        retryable: bool,
        safe_detail: str = "",
    ) -> RuntimeFault:
        if FAULT_CODE.fullmatch(code) is None or FAULT_CODE.fullmatch(subsystem) is None:
            raise ValueError("fault code and subsystem must be stable lowercase identifiers")
        detail = redact_text(safe_detail.replace("\r", " ").replace("\n", " "))[:240]
        fault = RuntimeFault(code, subsystem, retryable, detail, int(time.time()))
        self._faults.append(fault)
        return fault

    def snapshot(self) -> list[dict[str, object]]:
        return [asdict(fault) for fault in self._faults]
