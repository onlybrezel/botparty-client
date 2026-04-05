# Hardware Adapters

The hardware adapter translates BotParty control commands (`forward`, `backward`, `left`, `right`, `stop`) into signals for your specific motor controller or robotics platform.

Set `hardware.type` in `config.yaml` to the adapter name. The `hardware.options` block is passed directly to the adapter.

---

## Emergency stop

Every adapter implements `emergency_stop()`. The client calls it automatically when:

- A `control:emergency-stop` WebSocket event is received
- The latency threshold is exceeded (`safety.latency_threshold_ms`)
- The connection is lost

Your adapter's `emergency_stop` must be **synchronous, fast, and infallible**. It should cut motor power without any network calls or blocking I/O.

---

## Available adapters

| Adapter | Board / Platform | Interface |
|---------|-----------------|-----------|
| [`none`](none.md) | No movement | — |
| [`l298n`](l298n.md) | L298N dual H-bridge | GPIO |
| [`adafruit_pwm`](adafruit-pwm.md) | Adafruit PCA9685 PWM HAT | I2C |
| [`motor_hat`](motor-hat.md) | Adafruit Motor HAT | I2C |
| [`serial_board`](serial-board.md) | Arduino / any MCU | USB serial |
| [`mqtt_pub`](mqtt.md) | Any MQTT broker | TCP |
| [`pololu`](pololu.md) | Pololu DRV8835 | GPIO |
| [`mdd10`](mdd10.md) | Cytron MDD10 | GPIO + PWM |
| [`motozero`](motozero.md) | MotoZero | GPIO |
| [`thunderborg`](thunderborg.md) | PiBorg ThunderBorg | I2C |
| [`gopigo2`](gopigo.md) | GoPiGo 2 | I2C |
| [`gopigo3`](gopigo.md) | GoPiGo 3 | I2C |
| [`maestro_servo`](maestro-servo.md) | Pololu Maestro | USB |
| [`navq`](navq.md) | NXP NavQ / MAVSDK | MAVLink serial |
| [`cozmo`](cozmo-vector.md) | Anki Cozmo | Wi-Fi SDK |
| [`vector`](cozmo-vector.md) | Anki Vector | Wi-Fi SDK |
| [`owi_arm`](owi-arm.md) | OWI 535 Robotic Arm | USB HID |
| [`custom`](custom.md) | Your own hardware | Anything |

---

## Command reference

Controllers send these string commands. All adapters must handle at least the first five:

| Command | Description |
|---------|-------------|
| `forward` | Drive forward |
| `backward` | Drive backward |
| `left` | Turn left |
| `right` | Turn right |
| `stop` | Stop all motors immediately |
| `max_speed` | Toggle max speed mode (where supported) |
| `up` | Raise arm / lift accessory |
| `down` | Lower arm / drop accessory |
| `open` | Open gripper / claw |
| `close` | Close gripper / claw |

Custom hardware can define and handle any additional commands.
