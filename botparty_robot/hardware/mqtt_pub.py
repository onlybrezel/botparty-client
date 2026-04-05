"""MQTT publish adapter."""

from __future__ import annotations

import json as _json
from typing import Any

from .base import BaseHardware
from .common import optional_import


class HardwareAdapter(BaseHardware):
    profile_name = "mqtt_pub"
    description = "Publish BotParty commands to an MQTT topic"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.client = None
        self.mqtt = optional_import("paho.mqtt.client", "paho-mqtt")
        self.host = self.option_str("host", "localhost")
        self.port = self.option_int("port", 1883)
        self.topic = self.option_str("topic", "botparty/robot/command")
        self.username = self.options.get("username")
        self.password = self.options.get("password")
        self.stop_command = self.option_str("stop_command", "stop")
        self.payload_mode = self.option_str("payload_mode", "plain")

    def setup(self) -> None:
        if self.mqtt is None:
            return

        self.client = self.mqtt.Client(self.mqtt.CallbackAPIVersion.VERSION2, "botparty-robot")
        if self.username:
            self.client.username_pw_set(str(self.username), str(self.password or ""))
        try:
            self.client.connect(self.host, self.port, keepalive=60)
            self.client.loop_start()
            self.log.info("connected to %s:%s", self.host, self.port)
        except Exception as exc:
            self.log.warning("could not connect to MQTT broker: %s", exc)

    def _ensure_connected(self) -> bool:
        if self.client is None:
            return False
        if not self.client.is_connected():
            try:
                self.client.reconnect()
            except Exception as exc:
                self.log.warning("MQTT reconnect failed: %s", exc)
                return False
        return True

    def on_command(self, command: str, value: Any = None) -> None:
        if self.payload_mode == "json":
            payload: str = _json.dumps({"command": command, "value": value})
        else:
            payload = command if value is None else f"{command}:{value}"

        if not self._ensure_connected():
            self.log.info("topic=%s payload=%s", self.topic, payload)
            return

        self.client.publish(self.topic, payload)

    def emergency_stop(self) -> None:
        self.on_command(self.stop_command)
