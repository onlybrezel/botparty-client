# Adafruit PCA9685 PWM HAT

The Adafruit 16-channel PWM/Servo HAT uses a PCA9685 I2C chip. In a BotParty context it is typically wired for one drive motor (ESC) and one steering servo, making it ideal for RC car-style robots.

```yaml
hardware:
  type: "adafruit_pwm"
  options:
    address: "0x40"
    pwm_freq: 60
    drive_channel: 0
    steer_channel: 1
    neutral_drive: 335
    forward_drive: 445
    backward_drive: 270
    steer_left: 300
    steer_center: 400
    steer_right: 500
```

---

## How it works

The adapter sets PWM pulse widths (0–4095) on the specified channels. Each command pulse moves the drive motor to a target value, waits for `drive_seconds`, then returns to neutral. Steering is set at the same time and released after the move.

---

## Wiring

Connect the Adafruit HAT onto the Raspberry Pi GPIO header. It communicates over I2C (SDA/SCL). No additional GPIO wiring is needed.

```
Channel 0 → ESC signal wire (drive motor)
Channel 1 → Steering servo signal wire
Channel 2 → Auxiliary servo (optional)
```

Power your ESC and servos from the HAT's servo power rail. The HAT **does not** supply power to servos — you must connect a 5–6 V supply to the `V+` and `GND` pins on the terminal block.

---

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `address` | string | `"0x40"` | I2C address of the PCA9685 |
| `pwm_freq` | int | `60` | PWM frequency in Hz |
| `drive_channel` | int | `0` | PCA9685 channel for the drive motor ESC |
| `steer_channel` | int | `1` | PCA9685 channel for the steering servo |
| `aux_channel` | int | `2` | PCA9685 channel for an auxiliary servo |
| `neutral_drive` | int | `335` | PWM value for motor stopped (neutral) |
| `forward_drive` | int | `445` | PWM value for full forward |
| `forward_slow` | int | `345` | PWM value for slow forward |
| `backward_drive` | int | `270` | PWM value for full backward |
| `backward_slow` | int | `325` | PWM value for slow backward |
| `steer_left` | int | `300` | PWM value for full left steering |
| `steer_center` | int | `400` | PWM value for straight/center |
| `steer_right` | int | `500` | PWM value for full right steering |
| `aux_increment` | int | `300` | PWM step when `up` command received |
| `aux_decrement` | int | `400` | PWM step when `down` command received |

### Finding the right PWM values

The correct values depend on your specific ESC and servo. A good starting point:

1. Set `neutral_drive` so the motor is stopped when the client starts.
2. Increase `forward_drive` gradually until the robot moves at a comfortable speed.
3. Adjust `steer_left` / `steer_right` to match the servo travel.

Most hobby servos expect a PWM frequency of 50 Hz with pulse widths from ~1000 µs (full left) to ~2000 µs (full right). At 60 Hz that maps roughly to values 250–500 in PCA9685 counts.

---

## Dependencies

```bash
pip install Adafruit_PCA9685
```

Enable I2C on the Raspberry Pi if not already done:

```bash
sudo raspi-config   # Interface Options → I2C → Enable
```

Verify the board is detected:

```bash
sudo apt install -y i2c-tools
i2cdetect -y 1   # should show 0x40 (or your configured address)
```
