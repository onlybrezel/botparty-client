"""MQTT publish adapter."""

from __future__ import annotations

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

    def on_command(self, command: str, value: Any = None) -> None:
        payload: str
        if self.payload_mode == "json":
            import json

            payload = json.dumps({"command": command, "value": value})
        else:
            payload = command if value is None else f"{command}:{value}"
        if self.client is None:
            self.log.info("topic=%s payload=%s", self.topic, payload)
            return

        self.client.connect(self.host, self.port)
        self.client.publish(self.topic, payload)
        self.client.disconnect()

    def emergency_stop(self) -> None:
        self.on_command(self.stop_command)
