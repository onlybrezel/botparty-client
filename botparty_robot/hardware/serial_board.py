"""Serial bridge adapter."""

from __future__ import annotations

import base64
import json
import zlib
from typing import Any

from ..config import RobotConfig
from .base import BaseHardware
from .common import optional_import


class HardwareAdapter(BaseHardware):
    supported_commands = ("forward", "backward", "left", "right", "stop")
    motion_commands = supported_commands[:-1]
    profile_name = "serial_board"
    description = "Send commands to an attached microcontroller over serial"

    def __init__(self, config: RobotConfig) -> None:
        super().__init__(config)
        self.serial: Any | None = None
        self.serial_module = optional_import("serial", "pyserial")
        self.ports_module = optional_import("serial.tools.list_ports", "pyserial")
        self.device = self.option_str("device", "/dev/ttyUSB0")
        self.baud_rate = self.option_int("baud_rate", 115200)
        self.device_name = self.options.get("device_name")
        # Accept escape sequences like \n, \r\n from config (e.g. "\\r\\n").
        # Default is a plain newline.
        raw_ending = self.options.get("line_ending", "\n")
        if isinstance(raw_ending, str):
            self.line_ending = raw_ending.encode("utf-8").decode("unicode_escape")
        else:
            self.line_ending = "\n"
        self.stop_command = self.option_str("stop_command", "stop")
        self.payload_mode = self.option_str("payload_mode", "plain")
        self.protocol = self.option_str("protocol", "legacy")
        self.write_timeout_sec = self.option_float("write_timeout_sec", 1.0)
        self.ack_timeout_sec = self.option_float("ack_timeout_sec", 1.0)
        self._sequence = 0
        if self.protocol not in {"legacy", "framed_v1"}:
            raise ValueError("serial protocol must be legacy or framed_v1")
        if not 0.05 <= self.write_timeout_sec <= 5.0:
            raise ValueError("serial write_timeout_sec must be between 0.05 and 5")

    def setup(self) -> None:
        if self.serial_module is None:
            return

        if isinstance(self.device_name, str):
            found = self._search_device(self.device_name)
            if found:
                self.device = found

        self.serial = self.serial_module.Serial(
            self.device,
            self.baud_rate,
            timeout=self.ack_timeout_sec if self.protocol == "framed_v1" else 0,
            write_timeout=self.write_timeout_sec,
        )
        self.log.info("connected: %s @ %s", self.device, self.baud_rate)

    def _search_device(self, name: str) -> str | None:
        if self.ports_module is None:
            return None
        for port in self.ports_module.comports():
            haystacks = [port.description, port.hwid, getattr(port, "manufacturer", "")]
            if any(name.lower() in str(item).lower() for item in haystacks):
                return str(port.device)
        return None

    def _format_payload(self, command: str, value: Any) -> tuple[str, int | None]:
        if self.protocol == "framed_v1":
            self._sequence = (self._sequence + 1) % 2_147_483_647
            body = json.dumps(
                {"command": command, "id": self._sequence, "value": value},
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            checksum = zlib.crc32(body) & 0xFFFFFFFF
            encoded = base64.urlsafe_b64encode(body).decode("ascii")
            return f"BP1 {self._sequence} {checksum:08x} {encoded}", self._sequence
        if self.payload_mode == "json":
            return json.dumps({"command": command, "value": value}), None
        if value is None:
            return command, None
        return f"{command} {value}", None

    def _write_payload(self, payload: str, sequence: int | None) -> None:
        if self.serial is None or not getattr(self.serial, "is_open", True):
            raise RuntimeError("serial device is not connected")
        written = self.serial.write((payload + self.line_ending).encode("utf-8"))
        if written != len((payload + self.line_ending).encode("utf-8")):
            raise TimeoutError("serial write was incomplete")
        self.serial.flush()
        if sequence is None:
            return
        response = self.serial.readline().decode("ascii", errors="replace").strip()
        if response != f"ACK {sequence}":
            raise RuntimeError(f"serial acknowledgement mismatch for command {sequence}")

    def on_command(self, command: str, value: Any = None) -> None:
        payload, sequence = self._format_payload(command, value)
        if self.serial is None:
            raise RuntimeError("serial device is not connected")
        self.guarded_write(lambda: self._write_payload(payload, sequence))

    def emergency_stop(self) -> None:
        payload, sequence = self._format_payload(self.stop_command, None)
        self._write_payload(payload, sequence)

    def _close_resources(self) -> None:
        if self.serial is not None and self.serial.is_open:
            self.serial.close()
