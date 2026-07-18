"""MQTT publish adapter."""

from __future__ import annotations

import json as _json
import ssl
import threading
from pathlib import Path
from typing import Any

from ..config import RobotConfig
from .base import BaseHardware
from .common import optional_import


class HardwareAdapter(BaseHardware):
    supported_commands = ("forward", "backward", "left", "right", "stop")
    motion_commands = supported_commands[:-1]
    profile_name = "mqtt_pub"
    description = "Publish BotParty commands to an MQTT topic"

    def __init__(self, config: RobotConfig) -> None:
        super().__init__(config)
        self.client: Any | None = None
        self.mqtt = optional_import("paho.mqtt.client", "paho-mqtt")
        self.host = self.option_str("host", "localhost")
        self.tls_enabled = bool(
            self.options.get("tls", self.host not in {"localhost", "127.0.0.1", "::1"})
        )
        self.port = self.option_int("port", 8883 if self.tls_enabled else 1883)
        self.topic = self.option_str("topic", "botparty/robot/command")
        self.stop_topic = self.option_str("stop_topic", self.topic)
        self.status_topic = self.option_str("status_topic", "botparty/robot/status")
        self.username = self.options.get("username")
        self.password = self.options.get("password")
        self.stop_command = self.option_str("stop_command", "stop")
        self.payload_mode = self.option_str("payload_mode", "plain")
        self.qos = self.option_int("qos", 1)
        self.ack_timeout_sec = self.option_float("ack_timeout_sec", 1.0)
        self.ca_file = self.options.get("ca_file")

        if not self.tls_enabled and self.host not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("remote MQTT brokers require TLS")
        if self.qos not in {1, 2}:
            raise ValueError("MQTT motion delivery requires qos 1 or 2")

    def setup(self) -> None:
        if self.mqtt is None:
            return

        self.client = self.mqtt.Client(self.mqtt.CallbackAPIVersion.VERSION2, "botparty-robot")
        connected = threading.Event()

        def on_connect(
            _client: Any,
            _userdata: Any,
            _flags: Any,
            reason_code: Any,
            _properties: Any,
        ) -> None:
            if int(reason_code) == 0:
                connected.set()

        self.client.on_connect = on_connect
        self.client.will_set(self.status_topic, "offline", qos=self.qos, retain=True)
        if self.username:
            self.client.username_pw_set(str(self.username), str(self.password or ""))
        if self.tls_enabled:
            if self.ca_file is not None:
                ca_path = Path(str(self.ca_file))
                if not ca_path.is_file():
                    raise ValueError("MQTT ca_file is not a regular file")
            self.client.tls_set(
                ca_certs=str(self.ca_file) if self.ca_file is not None else None,
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )
            self.client.tls_insecure_set(False)
        self.client.connect(self.host, self.port, keepalive=60)
        self.client.loop_start()
        if not connected.wait(timeout=self.ack_timeout_sec):
            self.client.loop_stop()
            raise TimeoutError("MQTT connection acknowledgement timed out")
        self._publish(self.status_topic, "online", retain=True)
        self.log.info("connected to %s:%s", self.host, self.port)

    def _ensure_connected(self) -> None:
        if self.client is None:
            raise RuntimeError("MQTT client is not initialized")
        if not self.client.is_connected():
            raise RuntimeError("MQTT broker is not connected")

    def _publish(self, topic: str, payload: str, *, retain: bool = False) -> None:
        self._ensure_connected()
        client = self.client
        if client is None:
            raise RuntimeError("MQTT client is not initialized")
        info = client.publish(topic, payload, qos=self.qos, retain=retain)
        if getattr(info, "rc", 0) != 0:
            raise RuntimeError(f"MQTT publish was rejected with rc={info.rc}")
        info.wait_for_publish(timeout=self.ack_timeout_sec)
        is_published = getattr(info, "is_published", None)
        if callable(is_published) and not is_published():
            raise TimeoutError("MQTT publish acknowledgement timed out")

    def on_command(self, command: str, value: Any = None) -> None:
        if self.payload_mode == "json":
            payload: str = _json.dumps({"command": command, "value": value})
        else:
            payload = command if value is None else f"{command}:{value}"

        self.guarded_write(lambda: self._publish(self.topic, payload))

    def emergency_stop(self) -> None:
        self._publish(self.stop_topic, self.stop_command)

    def _close_resources(self) -> None:
        if self.client is not None:
            self.client.loop_stop()
            self.client.disconnect()
