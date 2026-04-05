# Other Hardware Adapters

---

## Pololu DRV8835

The Pololu DRV8835 Dual Motor Driver Kit for Raspberry Pi plugs directly onto the Pi GPIO header.

```yaml
hardware:
  type: "pololu"
  options:
    driving_speed: 200
    drive_time: 0.30
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `driving_speed` | int | `200` | Motor speed (0–480 for DRV8835) |
| `drive_time` | float | `0.3` | Pulse duration in seconds |

```bash
pip install drv8835-motor-driver-rpi
```

---

## Cytron MDD10

The MDD10 is a 10 A dual-channel motor driver. It uses GPIO direction pins and PWM for speed control.

```yaml
hardware:
  type: "mdd10"
  options:
    an1: 12        # PWM pin for motor 1 (BCM)
    an2: 13        # PWM pin for motor 2 (BCM)
    dig1: 26       # direction pin for motor 1 (BCM)
    dig2: 24       # direction pin for motor 2 (BCM)
    speed_percent: 60
    max_speed_percent: 100
    turn_delay: 0.20
    straight_delay: 0.35
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `an1` | int | `12` | PWM BCM pin for motor 1 |
| `an2` | int | `13` | PWM BCM pin for motor 2 |
| `dig1` | int | `26` | Direction BCM pin for motor 1 |
| `dig2` | int | `24` | Direction BCM pin for motor 2 |
| `speed_percent` | int | `60` | Normal speed as % of max duty cycle |
| `max_speed_percent` | int | `100` | Speed used when `max_speed` command received |
| `turn_delay` | float | `0.2` | Turn pulse duration |
| `straight_delay` | float | `0.35` | Drive pulse duration |

The `max_speed` command toggles between `speed_percent` and `max_speed_percent`.

```bash
sudo apt install python3-rpi.gpio
```

---

## MotoZero

The MotoZero is a 4-motor GPIO board from ThePiHut that stacks on the Raspberry Pi.

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
    # ... motor3 and motor4 use default BCM pins
```

All pin numbers default to the values silkscreened on the board. Only override if your board revision differs.

```bash
sudo apt install python3-rpi.gpio
```

---

## PiBorg ThunderBorg

The ThunderBorg is a motor controller with onboard battery monitoring.

```yaml
hardware:
  type: "thunderborg"
  options:
    left_motor_max: 1.0     # 0.0 – 1.0
    right_motor_max: 1.0
    sleep_time: 0.30
    # address: "0x15"       # only needed if changed from default
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `left_motor_max` | float | `1.0` | Max power for left motor (0.0–1.0) |
| `right_motor_max` | float | `1.0` | Max power for right motor (0.0–1.0) |
| `sleep_time` | float | `0.3` | Pulse duration |
| `address` | string | auto | I2C address if not default |

```bash
pip install thunderborg   # installs ThunderBorg3 or ThunderBorg depending on Python version
```

---

## GoPiGo 2 / GoPiGo 3

Dexter Industries GoPiGo robot kits are supported out of the box.

```yaml
hardware:
  type: "gopigo3"   # or "gopigo2"
  options:
    drive_time: 0.35
    turn_time: 0.15
```

```bash
pip install easygopigo3   # for GoPiGo 3
# GoPiGo 2 uses the gopigo package from Dexter Industries
```

---

## Pololu Maestro Servo Controller

Dual-servo differential drive using a Pololu Maestro USB servo controller.

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

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `left_channel` | int | `0` | Maestro channel for the left servo |
| `right_channel` | int | `1` | Maestro channel for the right servo |
| `center` | int | `6000` | Pulse value for servo stopped/center |
| `forward` | int | `12000` | Pulse value for full forward |
| `backward` | int | `0` | Pulse value for full backward |
| `straight_delay` | float | `0.35` | Duration of drive commands |
| `turn_delay` | float | `0.20` | Duration of turn commands |

```bash
pip install Maestro
```

---

## NXP NavQ / MAVSDK

MAVSDK offboard control for rovers running ArduRover or PX4.

```yaml
hardware:
  type: "navq"
  options:
    system_address: "serial:///dev/ttymxc2:921600"
    yaw_step: 45.0      # degrees per turn command
    thrust: 0.1         # 0.0 – 1.0
```

```bash
pip install mavsdk
```

---

## Anki Cozmo / Vector

Anki consumer robots are supported via their official Python SDKs.

```yaml
hardware:
  type: "cozmo"   # or "vector"
  options: {}
```

**Cozmo** requires the `cozmo` Python SDK and the Cozmo app running on a paired iOS/Android device.

**Vector** requires the `anki_vector` SDK and the robot's IP address configured in `~/.anki_vector/sdk_config.ini`.

```bash
pip install cozmo          # Cozmo
pip install anki_vector    # Vector
```

---

## OWI 535 USB Robotic Arm

The OWI 535 is a 5-axis USB robotic arm kit. BotParty maps the movement commands to arm segments.

```yaml
hardware:
  type: "owi_arm"
  options: {}
```

```bash
pip install pyusb
sudo apt install libusb-1.0-0
```

The `owi_arm` adapter requires write access to the USB device. Add a udev rule to avoid running as root:

```bash
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="1267", ATTR{idProduct}=="0000", MODE="0666"' | \
  sudo tee /etc/udev/rules.d/99-owi-arm.rules
sudo udevadm control --reload-rules
```
