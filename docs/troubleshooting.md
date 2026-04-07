# Troubleshooting

---

## Connection issues

### "claim_token not set" or "PASTE_YOUR_CLAIM_TOKEN_HERE"

You have not replaced the placeholder in `config.yaml`. Copy your claim token from the BotParty dashboard under **My Robots → Claim Token** and paste it in:

```yaml
server:
  claim_token: "eyJhbGc..."
```

### Robot shows as offline in the dashboard immediately after claiming

Check that `api_url` and `livekit_url` in `config.yaml` match the server your dashboard is connecting to. If you are self-hosting BotParty, these must point to your instance, not `botparty.live`.

### "Connection refused" on startup

The BotParty API server is not reachable. Verify:

```bash
curl https://botparty.live/api/v1/health   # or your api_url + /api/v1/health
```

### Client keeps reconnecting every few seconds

Usually a bad claim token (401) or a firewall blocking the WebSocket port. Enable debug logging to see the exact error:

```bash
BOTPARTY_LOG_LEVEL=debug python -m botparty_robot
```

---

## Camera / Video issues

### "No such file or directory: /dev/video0"

The camera is not connected or not detected by the kernel.

```bash
ls /dev/video*
dmesg | grep -i video
```

For USB cameras, try unplugging and reconnecting. Check `lsusb` to confirm the device is seen.

### Black screen / no video in browser

The camera is open but producing no frames. Test with FFmpeg directly:

```bash
ffmpeg -f v4l2 -i /dev/video0 -vframes 1 test.jpg && ls -lh test.jpg
```

If FFmpeg fails, the camera driver has an issue. Try the `opencv` video profile as a fallback.

### Very high latency on video (> 500 ms)

- Reduce resolution: `width: 640, height: 480`
- Reduce FPS: `fps: 15`
- Switch from YUYV to MJPG format: `fourcc: "MJPG"`
- Check CPU load: `htop` — if CPU is >90% the Pi cannot keep up

### libcamera-vid: "Failed to start camera"

On Raspberry Pi OS Bookworm the camera needs the correct overlay in `/boot/firmware/config.txt`:

```
# For Camera Module v2 (IMX219):
dtoverlay=imx219

# For Camera Module v3 (IMX708):
dtoverlay=imx708
```

After editing reboot: `sudo reboot`

Verify: `libcamera-hello --list-cameras`

### "Device or resource busy" on /dev/video0

Another process has the camera locked:

```bash
fuser /dev/video0
kill <PID>
```

---

## Hardware / Motor issues

### Robot does not move but client shows connected

Check that:
1. `hardware.type` is set correctly (not `none`)
2. Your motor driver board is powered
3. For GPIO adapters: `sudo usermod -aG gpio $USER` was run and you re-logged in

Test GPIO with Python:

```python
import RPi.GPIO as GPIO
GPIO.setmode(GPIO.BCM)
GPIO.setup(17, GPIO.OUT)
GPIO.output(17, GPIO.HIGH)   # should activate motor
import time; time.sleep(0.5)
GPIO.output(17, GPIO.LOW)
```

### GPIO permission denied

```bash
sudo usermod -aG gpio $USER
# then log out and back in, or:
newgrp gpio
```

### Serial adapter not found (/dev/ttyUSB0)

```bash
ls /dev/ttyUSB* /dev/ttyACM*
dmesg | tail -30   # check for USB enumeration errors
```

Grant access:

```bash
sudo usermod -aG dialout $USER
```

### I2C device not detected (adafruit_pwm, motor_hat, thunderborg)

```bash
sudo apt install i2c-tools
i2cdetect -y 1
```

Enable I2C if not visible:

```bash
sudo raspi-config   # Interface Options → I2C → Enable
```

---

## TTS / Audio issues

### No sound from speaker

```bash
# List playback devices
aplay -l

# Test the device
speaker-test -D default -t wav -c 1
aplay -D plughw:1,0 /usr/share/sounds/alsa/Front_Left.wav
```

Set `tts.playback_device` to match the device shown by `aplay -l`:

```yaml
tts:
  playback_device: "plughw:1,0"
```

### eSpeak / pico not found

```bash
sudo apt install espeak libttspico-utils
which espeak pico2wave
```

### TTS cuts off mid-sentence

Increase `tts.delay_ms` to give audio more time to play:

```yaml
tts:
  delay_ms: 200
```

---

## Performance

### High CPU usage on Raspberry Pi

1. Use `ffmpeg` profile instead of `opencv`
2. Use MJPG format at 720p instead of YUYV
3. Reduce FPS to 15–20
4. Disable TTS if not needed
5. Run overclocked if thermals allow (Pi 4: 2000 MHz)

### Robot commands feel laggy

- Check your internet connection speed from the Pi: `speedtest-cli`
- Control latency is primarily determined by network, not CPU

---

## Logs

Run with verbose logging to diagnose issues:

```bash
BOTPARTY_LOG_LEVEL=debug python -m botparty_robot 2>&1 | tee botparty.log
```

Key log prefixes:

| Prefix | What it covers |
|--------|---------------|
| `botparty.gateway` | WebSocket connection, events received |
| `botparty.camera` | Frame capture, publish to LiveKit |
| `botparty.hardware` | Command dispatch, GPIO / I2C calls |
| `botparty.tts` | TTS synthesis, audio playback |
