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
    my_speed: 50
```

The client looks for `hardware_custom.py` in the current working directory, next to `config.yaml`.

---

## Minimal template

```python
from __future__ import annotations

from typing import Any

from botparty_robot.hardware.base import BaseHardware


class HardwareAdapter(BaseHardware):
    profile_name = "custom"

    def setup(self) -> None:
        self.log.info("Custom hardware ready")

    def on_command(self, command: str, value: Any = None) -> None:
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
        try:
            self._stop_motors()
        except Exception:
            pass

    def _drive_forward(self) -> None:
        pass

    def _drive_backward(self) -> None:
        pass

    def _turn_left(self) -> None:
        pass

    def _turn_right(self) -> None:
        pass

    def _stop_motors(self) -> None:
        pass
```

The shipped template in [`botparty_robot/hardware/hardware_custom_example.py`](/home/julien/workspace/botparty-client/botparty_robot/hardware/hardware_custom_example.py) contains a fuller example including `self.command_context`.

---

## BaseHardware helpers

Your class inherits from `BaseHardware` which provides:

| Helper | Description |
|--------|-------------|
| `self.log` | `logging.Logger` named `botparty.hardware.<profile_name>` |
| `self.options` | `dict` of options from `config.yaml` |
| `self.command_context` | Metadata from the gateway such as `user.role`, `user.isRobotOwner`, `user.isSiteAdmin`, `user.isSiteModerator`, `robotId`, and chat payload fields |
| `self.option_int(key, default)` | Read an integer option with fallback |
| `self.option_float(key, default)` | Read a float option with fallback |
| `self.option_str(key, default)` | Read a string option with fallback |
| `self.option_pins(key)` | Read a list of integers |
| `self.matches(command, name)` | Case-insensitive command and alias matching |

---

## Emergency stop requirements

`emergency_stop()` is called in critical situations such as an explicit emergency stop, local shutdown, or a safety timeout. It must:

- Execute synchronously
- Return quickly
- Never raise an exception
- Cut all motor power even if other state is inconsistent
