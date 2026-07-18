# Serial Board — Arduino / USB Microcontroller

> **Release status: community.** No current controller-specific HIL evidence exists. Production
> movement is blocked.

The `serial_board` adapter sends commands over USB serial to an Arduino, Teensy, Raspberry Pi Pico
or another microcontroller.

The microcontroller firmware owns the motor behavior. The BotParty client sends the configured
commands over the serial port.

```yaml
hardware:
  type: "serial_board"
  options:
    device: "/dev/ttyUSB0"
    baud_rate: 115200
    payload_mode: "plain"    # or "json"
    line_ending: "\n"
    stop_command: "stop"
    protocol: "framed_v1"
    write_timeout_sec: 1.0
    ack_timeout_sec: 1.0
```

## How it works

For `framed_v1`, every command contains a sequence ID, CRC32 and base64url JSON body. The client
requires an exact `ACK <sequence>` response after the bytes have been flushed. Short writes,
timeouts and mismatched acknowledgements fail the command. The legacy line protocol remains
available only for compatibility and cannot prove delivery.

An acknowledgement proves firmware receipt, not de-energized motors. Motion remains disabled in
release metadata until the firmware stop implementation, independent cutoff and current HIL report
prove the complete stop path.

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

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `device` | string | `/dev/ttyUSB0` | Serial device path |
| `device_name` | string | `null` | Auto-detect by USB description (overrides `device`) |
| `baud_rate` | int | `115200` | Serial baud rate |
| `line_ending` | string | `"\n"` | Line terminator appended to each command. Use `"\\r\\n"` for Windows-style |
| `stop_command` | string | `"stop"` | Command to send on emergency stop |
| `payload_mode` | string | `"plain"` | `"plain"` or `"json"` |
| `protocol` | string | `"legacy"` | Use `"framed_v1"` for sequenced acknowledgements |
| `write_timeout_sec` | float | `1.0` | Maximum blocking write time |
| `ack_timeout_sec` | float | `1.0` | Maximum wait for `ACK <sequence>` |

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

Production firmware for `framed_v1` must decode the BP1 frame, verify CRC32, reject repeated or
out-of-order sequence IDs, apply the command, flush outputs and only then return `ACK <sequence>`.
Use a hardware watchdog that removes motor power when serial input or the controller loop stalls.

## Dependencies

```bash
pip install pyserial
```

Grant serial port access:

```bash
sudo ./scripts/install-botparty-client.sh --extras serial --device-groups dialout
```
