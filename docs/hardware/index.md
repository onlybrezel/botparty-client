# Hardware Adapters

> **Current release: media-only.** `none` is the only supported hardware profile. Moving profiles
> remain blocked until a release-specific HIL report promotes an exact adapter/device combination.

The hardware adapter translates BotParty control commands (`forward`, `backward`, `left`, `right`, `stop`) into signals for your specific motor controller or robotics platform.

Set `hardware.type` in `config.yaml` to the adapter name. Built-in `hardware.options` are validated
against a closed, range-limited schema. The generated machine-readable registry is
[`../generated/adapter-inventory.json`](../generated/adapter-inventory.json).

## Emergency stop

Every adapter implements `emergency_stop()`. The client calls it automatically when:

- A `control:emergency-stop` WebSocket event is received
- The client shuts down
- The local safety timeout expires without a fresh drive command

The stop call is synchronous and bounded. Any timeout, exception or still-running earlier stop is a
hard degraded state and prevents reset. A moving adapter is not release-supported until measured
HIL evidence proves that its full electrical path meets the configured stop deadline.

## Available adapters

| Adapter | Board / Platform | Interface |
|---------|-----------------|-----------|
| [`none`](other.md#none) | No movement | — |
| [`l298n`](l298n.md) | L298N dual H-bridge | GPIO |
| [`adafruit_pwm`](adafruit-pwm.md) | Adafruit PCA9685 PWM HAT | I2C |
| [`motor_hat`](motor-hat.md) | Adafruit Motor HAT | I2C |
| [`serial_board`](serial-board.md) | Arduino / any MCU | USB serial |
| [`mqtt_pub`](mqtt.md) | Any MQTT broker | TCP |
| [`pololu`](other.md#pololu-drv8835) | Pololu DRV8835 | GPIO |
| [`mc33926`](other.md#pololu-dual-mc33926) | Pololu dual MC33926 | GPIO |
| [`mdd10`](other.md#cytron-mdd10) | Cytron MDD10 | GPIO + PWM |
| [`motozero`](other.md#motozero) | MotoZero | GPIO |
| [`thunderborg`](other.md#piborg-thunderborg) | PiBorg ThunderBorg | I2C |
| [`gopigo2`](other.md#gopigo-2--gopigo-3) | GoPiGo 2 | I2C |
| [`gopigo3`](other.md#gopigo-2--gopigo-3) | GoPiGo 3 | I2C |
| [`megapi_board`](other.md#makeblock-megapi-board) | Makeblock MegaPi | USB serial |
| [`telly`](other.md#telly) | Telly | USB serial |
| [`max7219`](other.md#max7219-led-matrix) | MAX7219 LED matrix | SPI |
| [`maestro_servo`](other.md#pololu-maestro-servo-controller) | Pololu Maestro | USB |
| [`navq`](other.md#nxp-navq--mavsdk) | NXP NavQ / MAVSDK | MAVLink serial |
| [`cozmo`](other.md#anki-cozmo--vector) | Anki Cozmo | Wi-Fi SDK |
| [`vector`](other.md#anki-cozmo--vector) | Anki Vector | Wi-Fi SDK |
| [`owi_arm`](other.md#owi-535-usb-robotic-arm) | OWI 535 Robotic Arm | USB HID |
| [`custom`](custom.md) | Your own hardware | Anything |

## Command reference

Controllers send these canonical commands. The advertised command and motion sets are
adapter-specific; consult the generated inventory rather than assuming every adapter can drive.

| Command | Description |
|---------|-------------|
| `forward` | Drive forward |
| `backward` | Drive backward |
| `left` | Turn left |
| `right` | Turn right |
| `stop` | Stop all motors immediately |
| `max_speed` | Optional speed-mode command; some legacy adapters expect exact strings such as `MAXSPEED` |
| `up` | Raise arm / lift accessory (`lift_up` aliases are also accepted by many adapters) |
| `down` | Lower arm / drop accessory (`lift_down` aliases are also accepted by many adapters) |
| `open` | Open gripper / claw |
| `close` | Close gripper / claw |
| `camera_up` / `camera_down` | Head/camera tilt commands for adapters that expose them |

Custom hardware can define and handle any additional commands.
