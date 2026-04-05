# Serial Board — Arduino / USB Microcontroller

The `serial_board` adapter sends control commands as text strings over a USB serial connection to an Arduino, Teensy, Raspberry Pi Pico, or any other microcontroller.

This is the most flexible hardware option: you write the firmware on the MCU side and the BotParty client just sends commands over the serial port.

```yaml
hardware:
  type: "serial_board"
  options:
    device: "/dev/ttyUSB0"
    baud_rate: 115200
    payload_mode: "plain"    # or "json"
    line_ending: "\n"
    stop_command: "stop"
```

---

## How it works

On each `on_command` call the adapter formats the command into a string and writes it followed by `line_ending` to the serial port.

### Payload modes

**`plain`** (default)

```
forward\n
left\n
stop\n
```

If the command carries a value (e.g. speed):

```
speed 75\n
```

**`json`**

```json
{"command": "forward", "value": null}\n
{"command": "speed", "value": 75}\n
```

---

## Finding your device

```bash
# After plugging in the Arduino:
ls /dev/ttyUSB* /dev/ttyACM*
# or
dmesg | tail -20
```

Common device names:

| Board | Typical device |
|-------|---------------|
| Arduino Uno / Mega | `/dev/ttyACM0` |
| Arduino Nano (CH340 clone) | `/dev/ttyUSB0` |
| Arduino Nano (FTDI) | `/dev/ttyUSB0` |
| Raspberry Pi Pico | `/dev/ttyACM0` |
| Teensy | `/dev/ttyACM0` |

### Auto-detection by name

Instead of a fixed device path you can search by the board's USB description string:

```yaml
hardware:
  type: "serial_board"
  options:
    device_name: "Arduino Uno"   # partial match against USB description
    baud_rate: 115200
```

The adapter will scan all serial ports and connect to the first one whose description or hardware ID contains `"Arduino Uno"` (case-insensitive).

---

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `device` | string | `/dev/ttyUSB0` | Serial device path |
| `device_name` | string | `null` | Auto-detect by USB description (overrides `device`) |
| `baud_rate` | int | `115200` | Serial baud rate |
| `line_ending` | string | `"\n"` | Line terminator appended to each command. Use `"\\r\\n"` for Windows-style |
| `stop_command` | string | `"stop"` | Command to send on emergency stop |
| `payload_mode` | string | `"plain"` | `"plain"` or `"json"` |

---

## Arduino firmware example

```cpp
// Minimal BotParty serial receiver
void setup() {
  Serial.begin(115200);
  // set up motor pins here
}

void loop() {
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();

    if (cmd == "forward")       { /* drive forward */ }
    else if (cmd == "backward") { /* drive backward */ }
    else if (cmd == "left")     { /* turn left */ }
    else if (cmd == "right")    { /* turn right */ }
    else if (cmd == "stop")     { /* stop motors */ }
  }
}
```

---

## Dependencies

```bash
pip install pyserial
```

Grant serial port access:

```bash
sudo usermod -aG dialout $USER   # log out and in after this
```
