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
        robot_id = self.command_context.get("robotId")

        self.log.debug(
            "Command from %s role=%s robot=%s: %s value=%s",
            username,
            role,
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

        elif self.matches(command, "stop"):
            self.emergency_stop()

        else:
            self.log.debug("Unknown command: %s (value=%s)", command, value)

    def emergency_stop(self) -> None:
        """Stop all motors immediately.  Must never fail."""
        self.log.info("Emergency stop")
        # e.g. GPIO.output(FORWARD_PIN, GPIO.LOW)
        # e.g. GPIO.output(BACKWARD_PIN, GPIO.LOW)

