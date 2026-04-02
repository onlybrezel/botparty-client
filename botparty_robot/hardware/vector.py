"""Vector robot adapter."""

from __future__ import annotations

import time
from typing import Any

from .base import BaseHardware
from .common import optional_import

_VECTOR_ROBOT = None


def get_vector_robot():
    return _VECTOR_ROBOT


class HardwareAdapter(BaseHardware):
    profile_name = "vector"
    description = "Anki Vector control adapter"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.anki_vector = optional_import("anki_vector", "anki_vector")
        self.vector = None
        self.forward_speed = self.option_int("forward_speed", 75)
        self.turn_speed = self.option_int("turn_speed", 50)
        self.volume = self.option_int("volume", 100)

    def setup(self) -> None:
        global _VECTOR_ROBOT
        if self.anki_vector is None:
            return
        try:
            serial = self.options.get("serial")
            self.vector = self.anki_vector.AsyncRobot(serial=serial) if serial else self.anki_vector.AsyncRobot()
            self.vector.connect()
            self.vector.audio.set_master_volume(self.volume / 100)
            _VECTOR_ROBOT = self.vector
        except Exception as exc:
            self.log.warning("setup failed: %s", exc)
            self.vector = None

    def on_command(self, command: str, value: Any = None) -> None:
        if self.vector is None:
            self.log.info("command=%s value=%s", command, value)
            return
        try:
            if self.matches(command, "forward"):
                self.vector.motors.set_wheel_motors(self.forward_speed, self.forward_speed, self.forward_speed * 4, self.forward_speed * 4)
                time.sleep(0.7)
                self.vector.motors.set_wheel_motors(0, 0)
            elif self.matches(command, "backward"):
                self.vector.motors.set_wheel_motors(-self.forward_speed, -self.forward_speed, -self.forward_speed * 4, -self.forward_speed * 4)
                time.sleep(0.7)
                self.vector.motors.set_wheel_motors(0, 0)
            elif self.matches(command, "left"):
                self.vector.motors.set_wheel_motors(-self.turn_speed, self.turn_speed, -self.turn_speed * 4, self.turn_speed * 4)
                time.sleep(0.5)
                self.vector.motors.set_wheel_motors(0, 0)
            elif self.matches(command, "right"):
                self.vector.motors.set_wheel_motors(self.turn_speed, -self.turn_speed, self.turn_speed * 4, -self.turn_speed * 4)
                time.sleep(0.5)
                self.vector.motors.set_wheel_motors(0, 0)
            elif self.matches(command, "lift_up"):
                self.vector.set_lift_height(height=1).wait_for_completed()
            elif self.matches(command, "lift_down"):
                self.vector.set_lift_height(height=0).wait_for_completed()
            elif self.matches(command, "head_up"):
                self.vector.behavior.set_head_angle(45)
                time.sleep(0.35)
                self.vector.behavior.set_head_angle(0)
            elif self.matches(command, "head_down"):
                self.vector.behavior.set_head_angle(-22.0)
                time.sleep(0.35)
                self.vector.behavior.set_head_angle(0)
            elif command.startswith("say:"):
                self.vector.behavior.say_text(command.split(":", 1)[1])
            elif command in {"sayhi", "saywatch", "saylove", "saybye", "sayhappy", "saysad", "sayhowru"}:
                phrases = {
                    "sayhi": "hi! I'm Vector!",
                    "saywatch": "watch this",
                    "saylove": "i love you",
                    "saybye": "bye",
                    "sayhappy": "I'm happy",
                    "saysad": "I'm sad",
                    "sayhowru": "how are you?",
                }
                self.vector.behavior.say_text(phrases[command])
            elif self.matches(command, "stop"):
                self.emergency_stop()
        except Exception as exc:
            self.log.warning("command failed: %s", exc)

    def emergency_stop(self) -> None:
        if self.vector is not None:
            self.vector.motors.set_wheel_motors(0, 0)
