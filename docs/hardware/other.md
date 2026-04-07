# Other Hardware Adapters

---

## None

The `none` adapter is a no-op stub that logs every command and does nothing else. Use it when you want to test video and TTS without any physical hardware connected.

```yaml
hardware:
  type: "none"
  options: {}
```

No additional dependencies required.

---

## Pololu DRV8835

```yaml
hardware:
  type: "pololu"
  options:
    driving_speed: 200
    drive_time: 0.30
```

| Option | Type | Default |
|--------|------|---------|
| `driving_speed` | int | `200` |
| `drive_time` | float | `0.3` |

```bash
pip install drv8835-motor-driver-rpi
```

---

## Pololu Dual MC33926

```yaml
hardware:
  type: "mc33926"
  options:
    driving_speed: 180
    drive_time: 0.30
```

| Option | Type | Default |
|--------|------|---------|
| `driving_speed` | int | `180` |
| `drive_time` | float | `0.3` |

```bash
pip install dual-mc33926-motor-driver-rpi
```

---

## Cytron MDD10

```yaml
hardware:
  type: "mdd10"
  options:
    an1: 12
    an2: 13
    dig1: 26
    dig2: 24
    speed_percent: 60
    max_speed_percent: 100
    turn_delay: 0.20
    straight_delay: 0.35
```

The adapter enables `max_speed_percent` when it receives the legacy exact command `MAXSPEED`.

```bash
sudo apt install python3-rpi.gpio
```

---

## MotoZero

```yaml
hardware:
  type: "motozero"
  options:
    motor_delay: 0.25
    motor1a: 24
    motor1b: 25
    motor1enable: 12
    motor2a: 27
    motor2b: 17
    motor2enable: 13
```

```bash
sudo apt install python3-rpi.gpio
```

---

## PiBorg ThunderBorg

```yaml
hardware:
  type: "thunderborg"
  options:
    left_motor_max: 1.0
    right_motor_max: 1.0
    sleep_time: 0.30
```

```bash
pip install thunderborg
```

---

## GoPiGo 2 / GoPiGo 3

```yaml
hardware:
  type: "gopigo3"   # or "gopigo2"
  options:
    drive_time: 0.35
    turn_time: 0.15
```

```bash
pip install easygopigo3   # GoPiGo 3
```

---

## Makeblock MegaPi Board

```yaml
hardware:
  type: "megapi_board"
  options:
    motor_time: 0.20
    driving_speed: 150
    arm_speed: 50
    grabber_speed: 50
    left_track_port: 2
    right_track_port: 3
    arm_port: 1
    grabber_port: 4
```

```bash
pip install megapi
```

---

## Telly

`telly` is a convenience preset built on top of [`serial_board`](serial-board.md). If `device_name` is not set, it auto-searches for a serial device whose USB description contains `Telly`.

```yaml
hardware:
  type: "telly"
  options:
    baud_rate: 115200
```

---

## MAX7219 LED Matrix

The `max7219` adapter drives a MAX7219 SPI LED matrix for simple expressions and status displays.

```yaml
hardware:
  type: "max7219"
  options:
    bus: 0
    device: 0
    rotate: 0
    max_speed_hz: 1000000
```

Recognized commands include `LED_OFF`, `LED_FULL`, `LED_LOW`, `LED_MED`, `LED_E_SMILEY`, `LED_E_SAD`, `LED_E_TONGUE`, and `LED_E_SURPRISED`.

```bash
pip install spidev
```

---

## Pololu Maestro Servo Controller

```yaml
hardware:
  type: "maestro_servo"
  options:
    left_channel: 0
    right_channel: 1
    center: 6000
    forward: 12000
    backward: 0
    straight_delay: 0.35
    turn_delay: 0.20
```

```bash
pip install Maestro
```

---

## NXP NavQ / MAVSDK

```yaml
hardware:
  type: "navq"
  options:
    system_address: "serial:///dev/ttymxc2:921600"
    yaw_step: 45.0
    thrust: 0.1
```

```bash
pip install mavsdk
```

---

## Anki Cozmo / Vector

```yaml
hardware:
  type: "cozmo"   # or "vector"
  options: {}
```

Useful options:

- `cozmo`: `forward_speed`, `volume`, `colour`
- `vector`: `forward_speed`, `turn_speed`, `volume`, `serial`

```bash
pip install cozmo          # Cozmo
pip install anki_vector    # Vector
```

---

## OWI 535 USB Robotic Arm

```yaml
hardware:
  type: "owi_arm"
  options:
    step_seconds: 0.15
    vendor_id: 0x1267
    product_id: 0x0000
```

```bash
pip install pyusb
sudo apt install libusb-1.0-0
```

To avoid running as root, add a udev rule:

```bash
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="1267", ATTR{idProduct}=="0000", MODE="0666"' | \
  sudo tee /etc/udev/rules.d/99-owi-arm.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Supported commands include `forward`, `backward`, `left`, `right`, `lift_up`, `lift_down`, `head_up`, `head_down`, `open`, and `close`.
