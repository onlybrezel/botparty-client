"""Custom hardware adapter template.

HOW TO USE
----------
1. Copy this file to the same directory as your config.yaml:
       cp hardware_custom_example.py hardware_custom.py

2. Open hardware_custom.py and fill in your motor logic below.
   The class must stay named HardwareAdapter.

3. In config.yaml set:
       hardware:
         type: "custom"
         options:
           speed: 50    # any extra options your code reads via self.options

4. Run from that same directory:
       python -m botparty_robot

The client will automatically find hardware_custom.py next to config.yaml.

STANDARD COMMANDS
-----------------
The BotParty web controller sends these string commands:
    forward, backward, left, right, stop

Custom buttons defined in the dashboard can send any arbitrary string.

WIDGET VALUES (SLIDER / XY)
---------------------------
Buttons with a widget spec in the dashboard send values in `value`:
    - slider: number (for example 35 or -80)
    - xy pad: dict with x and y (for example {"x": 42, "y": -15})

Use helper methods from BaseHardware:
    - self.value_float(value, default=0.0)
    - self.value_xy(value, default=(0.0, 0.0))

SELF.OPTIONS
------------
Everything under hardware.options in config.yaml is available as self.options.
For example:  self.options.get("speed", 100)
"""

from __future__ import annotations

from typing import Any

from botparty_robot.hardware.base import BaseHardware


class HardwareAdapter(BaseHardware):
    """Replace the method bodies below with your actual motor code."""

    profile_name = "custom"
    description = "Custom hardware adapter loaded from hardware_custom.py"

    def setup(self) -> None:
        """Run once at startup. Initialize GPIO, serial, or whatever your hardware needs."""
        speed = self.options.get("speed", 100)
        self.log.info("HardwareAdapter ready (speed=%s)", speed)

    def on_command(self, command: str, value: Any = None) -> None:
        """Handle a control command from the browser.

        Use self.matches(command, "name1", "name2") for case-insensitive matching.
        Metadata from the gateway is available on self.command_context.
        """
        user = self.command_context.get("user", {})
        username = user.get("username") or "anonymous"
        role = user.get("role") or "guest"
        is_robot_owner = bool(user.get("isRobotOwner"))
        is_site_admin = bool(user.get("isSiteAdmin"))
        is_site_moderator = bool(user.get("isSiteModerator"))
        robot_id = self.command_context.get("robotId")

        self.log.debug(
            "Command from %s role=%s owner=%s site_admin=%s site_moderator=%s robot=%s: %s value=%s",
            username,
            role,
            is_robot_owner,
            is_site_admin,
            is_site_moderator,
            robot_id,
            command,
            value,
        )

        if self.matches(command, "chat"):
            payload = value if isinstance(value, dict) else {}
            message = payload.get("message") or ""
            sender = payload.get("sender") or username
            anonymous = bool(payload.get("anonymous"))
            self.log.info(
                "Chat from %s anonymous=%s: %s",
                sender,
                anonymous,
                message,
            )

        elif self.matches(command, "forward"):
            self.log.info("Moving forward")
            # e.g. GPIO.output(FORWARD_PIN, GPIO.HIGH)

        elif self.matches(command, "backward"):
            self.log.info("Moving backward")

        elif self.matches(command, "left"):
            self.log.info("Turning left")

        elif self.matches(command, "right"):
            self.log.info("Turning right")

        elif self.matches(command, "set_speed", "speed"):
            speed = self.value_float(value, default=0.0)
            self.log.info("Speed slider value: %.2f", speed)
            # Example: set motor PWM scale from slider value.

        elif self.matches(command, "arm_xy", "ptz_xy"):
            x, y = self.value_xy(value, default=(0.0, 0.0))
            self.log.info("XY pad value x=%.2f y=%.2f", x, y)
            # Example: map x/y to pan/tilt servo target angles.

        elif self.matches(command, "stop"):
            self.emergency_stop()

        else:
            self.log.debug("Unknown command: %s (value=%s)", command, value)

    def emergency_stop(self) -> None:
        """Stop all motors immediately.  Must never fail."""
        self.log.info("Emergency stop")
        # e.g. GPIO.output(FORWARD_PIN, GPIO.LOW)
        # e.g. GPIO.output(BACKWARD_PIN, GPIO.LOW)
