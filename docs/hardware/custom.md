# Custom Hardware Adapter

If your motor controller is not in the built-in list, write a `hardware_custom.py` file that the client will discover automatically.

---

## Quick start

Copy the example template included in the package:

```bash
cp botparty_robot/hardware/hardware_custom_example.py hardware_custom.py
```

Then set the hardware type in `config.yaml`:

```yaml
hardware:
  type: "custom"
  options:
    # Any keys here become self.options inside your adapter
    my_speed: 50
```

The client looks for `hardware_custom.py` in the current working directory (next to `config.yaml`).

---

## Adapter template

```python
"""Custom hardware adapter for BotParty."""

from __future__ import annotations

from typing import Any
from botparty_robot.hardware.base import BaseHardware


class HardwareAdapter(BaseHardware):
    profile_name = "custom"

    def __init__(self, config) -> None:
        super().__init__(config)
        # Read your config options like this:
        self.speed = self.option_int("my_speed", 50)

    def setup(self) -> None:
        """Called once at startup. Open serial ports, init I2C, etc."""
        self.log.info("Custom hardware ready")

    def on_command(self, command: str, value: Any = None) -> None:
        """Called for every control event from the server."""
        if self.matches(command, "forward"):
            self._drive_forward()
        elif self.matches(command, "backward"):
            self._drive_backward()
        elif self.matches(command, "left"):
            self._turn_left()
        elif self.matches(command, "right"):
            self._turn_right()
        elif self.matches(command, "stop"):
            self.emergency_stop()
        else:
            self.log.debug("unknown command: %s", command)

    def emergency_stop(self) -> None:
        """MUST be implemented. Called on any unsafe condition."""
        # Stop everything, immediately, no exceptions
        self._stop_motors()

    # ------- your implementation below -------

    def _drive_forward(self) -> None:
        pass   # your motor code here

    def _drive_backward(self) -> None:
        pass

    def _turn_left(self) -> None:
        pass

    def _turn_right(self) -> None:
        pass

    def _stop_motors(self) -> None:
        pass
```

---

## BaseHardware helpers

Your class inherits from `BaseHardware` which provides:

| Helper | Description |
|--------|-------------|
| `self.log` | `logging.Logger` named `botparty.hardware` |
| `self.options` | `dict` of options from `config.yaml` |
| `self.option_int(key, default)` | Read an integer option with fallback |
| `self.option_float(key, default)` | Read a float option with fallback |
| `self.option_str(key, default)` | Read a string option with fallback |
| `self.option_pins(key)` | Read a list of integers (GPIO pins) |
| `self.matches(command, name)` | Case-insensitive command name comparison |

---

## Multiple custom adapters

If you have several robots with different hardware, give each adapter a unique file name and reference it in `config.yaml`:

```yaml
hardware:
  type: "my_tank"    # loads hardware_my_tank.py from cwd
  options: {}
```

The client will look for `hardware_my_tank.py` in the current directory.

---

## Emergency stop requirements

`emergency_stop()` is called in critical situations — loss of connection, excessive latency, or a server-initiated stop signal. It must:

- Execute **synchronously** (no `await`)
- Return in **< 50 ms**
- Never raise an exception
- Cut all motor power even if other state is inconsistent

```python
def emergency_stop(self) -> None:
    try:
        self._stop_motors()
    except Exception:
        pass   # swallow everything - stopping is non-negotiable
```
