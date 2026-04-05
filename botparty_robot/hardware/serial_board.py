"""Serial bridge adapter."""

from __future__ import annotations

from typing import Any

from .base import BaseHardware
from .common import optional_import


class HardwareAdapter(BaseHardware):
    profile_name = "serial_board"
    description = "Send commands to an attached microcontroller over serial"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.serial = None
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

    def setup(self) -> None:
        if self.serial_module is None:
            return

        if isinstance(self.device_name, str):
            found = self._search_device(self.device_name)
            if found:
                self.device = found

        try:
            self.serial = self.serial_module.Serial(self.device, self.baud_rate, timeout=0, write_timeout=0)
            self.log.info("connected: %s @ %s", self.device, self.baud_rate)
        except Exception as exc:
            self.log.warning("could not open %s: %s", self.device, exc)

    def _search_device(self, name: str) -> str | None:
        if self.ports_module is None:
            return None
        for port in self.ports_module.comports():
            haystacks = [port.description, port.hwid, getattr(port, "manufacturer", "")]
            if any(name.lower() in str(item).lower() for item in haystacks):
                return port.device
        return None

    def _format_payload(self, command: str, value: Any) -> str:
        if self.payload_mode == "json":
            import json

            return json.dumps({"command": command, "value": value})
        if value is None:
            return command
        return f"{command} {value}"

    def on_command(self, command: str, value: Any = None) -> None:
        payload = self._format_payload(command, value)
        if self.serial is None:
            self.log.info("command=%s", payload)
            return
        self.serial.write((payload + self.line_ending).encode("utf-8"))
        self.serial.flush()

    def emergency_stop(self) -> None:
        self.on_command(self.stop_command)
