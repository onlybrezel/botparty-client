"""Cozmo robot adapter."""

from __future__ import annotations

import threading
import time
from typing import Any

from .base import BaseHardware
from .common import optional_import

_COZMO_ROBOT = None


def get_cozmo_robot():
    return _COZMO_ROBOT


class HardwareAdapter(BaseHardware):
    profile_name = "cozmo"
    description = "Anki Cozmo control adapter"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.cozmo = optional_import("cozmo", "cozmo[camera]")
        self.robot = None
        self.forward_speed = self.option_int("forward_speed", 75)
        self.volume = self.option_int("volume", 100)
        self.colour = bool(self.options.get("colour", True))

    def setup(self) -> None:
        if self.cozmo is None:
            return
        self.cozmo.robot.Robot.drive_off_charger_on_connect = False
        threading.Thread(target=self._connect, daemon=True).start()

    def _connect(self) -> None:
        try:
            self.cozmo.connect(self._run)
        except Exception as exc:
            self.log.warning("setup failed: %s", exc)

    def _run(self, conn) -> None:
        global _COZMO_ROBOT
        self.robot = conn.wait_for_robot()
        self.robot.enable_stop_on_cliff(True)
        self.robot.camera.color_image_enabled = self.colour
        self.robot.set_robot_volume(self.volume / 100)
        _COZMO_ROBOT = self.robot
        while True:
            time.sleep(1)

    def on_command(self, command: str, value: Any = None) -> None:
        if self.robot is None or self.cozmo is None:
            self.log.info("command=%s value=%s", command, value)
            return
        degrees = self.cozmo.util.degrees
        distance_mm = self.cozmo.util.distance_mm
        speed_mmps = self.cozmo.util.speed_mmps
        try:
            if self.matches(command, "forward"):
                self.robot.drive_straight(distance_mm(10), speed_mmps(self.forward_speed), False, True).wait_for_completed()
            elif self.matches(command, "backward"):
                self.robot.drive_straight(distance_mm(-10), speed_mmps(self.forward_speed), False, True).wait_for_completed()
            elif self.matches(command, "left"):
                self.robot.turn_in_place(degrees(15), False).wait_for_completed()
            elif self.matches(command, "right"):
                self.robot.turn_in_place(degrees(-15), False).wait_for_completed()
            elif self.matches(command, "lift_up"):
                self.robot.set_lift_height(1.0).wait_for_completed()
            elif self.matches(command, "lift_down"):
                self.robot.set_lift_height(0.0).wait_for_completed()
            elif self.matches(command, "head_down"):
                self.robot.set_head_angle(degrees(0)).wait_for_completed()
            elif self.matches(command, "head_up"):
                self.robot.set_head_angle(degrees(44.5)).wait_for_completed()
            elif command == "v":
                self.robot.play_anim("anim_poked_giggle").wait_for_completed()
            elif command in {"sayhi", "saywatch", "saylove", "saybye", "sayhappy", "saysad", "sayhowru"}:
                phrases = {
                    "sayhi": "hi! I'm Cozmo!",
                    "saywatch": "watch this",
                    "saylove": "i love you",
                    "saybye": "bye",
                    "sayhappy": "I'm happy",
                    "saysad": "I'm sad",
                    "sayhowru": "how are you?",
                }
                self.robot.say_text(phrases[command]).wait_for_completed()
            elif command.startswith("say:"):
                self.robot.say_text(command.split(":", 1)[1]).wait_for_completed()
            elif command == "lightcubes":
                for cube_id, light in (
                    (self.cozmo.objects.LightCube1Id, self.cozmo.lights.red_light),
                    (self.cozmo.objects.LightCube2Id, self.cozmo.lights.green_light),
                    (self.cozmo.objects.LightCube3Id, self.cozmo.lights.blue_light),
                ):
                    cube = self.robot.world.get_light_cube(cube_id)
                    if cube is not None:
                        cube.set_lights(light)
            elif command == "dimcubes":
                for cube_id in (
                    self.cozmo.objects.LightCube1Id,
                    self.cozmo.objects.LightCube2Id,
                    self.cozmo.objects.LightCube3Id,
                ):
                    cube = self.robot.world.get_light_cube(cube_id)
                    if cube is not None:
                        cube.set_lights_off()
            elif self.matches(command, "stop"):
                self.emergency_stop()
        except Exception as exc:
            self.log.warning("command failed: %s", exc)

    def emergency_stop(self) -> None:
        if self.robot is not None:
            try:
                self.robot.stop_all_motors()
            except Exception:
                pass
