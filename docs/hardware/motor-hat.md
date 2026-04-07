# Adafruit Motor HAT

The Adafruit DC + Stepper Motor HAT uses an I2C interface and can drive up to 4 DC motors or 2 stepper motors. It is commonly used on Raspberry Pi-based robots with tank/differential drive.

```yaml
hardware:
  type: "motor_hat"
  options:
    address: "0x60"
    i2c_bus: 1
    left_motors: [1, 2]
    right_motors: [3, 4]
    drive_speed: 180
    drive_time: 0.35
    turn_speed: 180
    turn_time: 0.20
```

---

## How it works

The adapter uses the Adafruit MotorHAT Python library. On each command it sets motor speed and direction, waits for the configured duration, then releases all motors. This is open-loop; there is no encoder feedback.

Channels 1–4 map to the four motor terminals on the HAT (M1–M4).

---

## Wiring

Stack the HAT directly onto the Raspberry Pi GPIO header. Connect DC motors to the M1–M4 screw terminals. Power the HAT via the 5.5 mm barrel jack (5–12 V).

For a typical two-wheel differential drive:

```
M1, M2 → left side motors
M3, M4 → right side motors
```

If you only have two motors use `left_motors: [1]` and `right_motors: [2]`.

---

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `address` | string/int | `0x60` | I2C address of the Motor HAT |
| `i2c_bus` | int | auto, usually `1` | Explicit I2C bus number. Set this to `1` on Raspberry Pi if the Adafruit library cannot detect the default bus automatically. |
| `left_motors` | list[int] | `[1, 2]` | HAT channels (1–4) for the left side |
| `right_motors` | list[int] | `[3, 4]` | HAT channels (1–4) for the right side |
| `drive_speed` | int | `180` | Motor speed 0–255 for drive commands |
| `turn_speed` | int | `180` | Motor speed 0–255 for turn commands |
| `drive_time` | float | `0.35` | Duration of drive commands in seconds |
| `turn_time` | float | `0.20` | Duration of turn commands in seconds |
| `up_motor` | int | `0` | Optional accessory motor channel for `up`/`down` commands |
| `open_motor` | int | `0` | Optional accessory motor channel for `open`/`close` commands |

---

## Dependencies

```bash
pip install Adafruit_MotorHAT
```

On Raspberry Pi OS you will usually also want:

```bash
sudo apt install -y i2c-tools python3-smbus
```

Enable I2C:

```bash
sudo raspi-config   # Interface Options → I2C → Enable
i2cdetect -y 1      # should show 0x60
```

If you still see `Could not determine default I2C bus for platform.`, set `i2c_bus: 1` explicitly in `config.yaml`.
