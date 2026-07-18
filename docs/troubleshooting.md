# Troubleshooting

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

Keep the robot safety-latched and identify the owner before stopping anything:

```bash
sudo systemctl stop botparty-robot.service
sudo fuser -v /dev/video0
ps -fp <verified-PID>
sudo kill -TERM <verified-PID>
sudo fuser -v /dev/video0
```

Use `KILL` only after a verified non-client process ignores `TERM`; then inspect its cleanup and
restart policy before restarting BotParty. Confirm the camera is free, run `doctor`, start the
service and reset safety only with the area clear.

## Hardware / Motor issues

### Robot does not move but client shows connected

Check that:
1. `hardware.type` is set correctly (not `none`)
2. Your motor driver board is powered
3. For GPIO adapters: `sudo usermod -aG gpio $USER` was run and you re-logged in

Run `botparty-robot --config config.yaml doctor` for a non-moving device and permission check. Perform active GPIO tests only with the robot lifted, the area clear and a hard-wired cutoff within reach.

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

If messages are overlapping or starting too aggressively, add a small delay before playback starts:

```yaml
tts:
  delay_ms: 200
```

## Performance

### High CPU usage on Raspberry Pi

1. Use `ffmpeg` profile instead of `opencv`
2. Use MJPG format at 720p instead of YUYV
3. Reduce FPS to 15–20
4. Disable TTS if not needed
5. Check throttling, cooling, power supply and the board vendor's supported operating limits.
   Do not use overclocking as a production recovery step.

### Robot commands feel laggy

- Check your internet connection speed from the Pi: `speedtest-cli`
- Check `/health` for stale commands, queue drops, control reconnects and safety stops.
- Keep CPU and memory within the device-class budget in [Performance](performance.md).

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
| `botparty.client` | Authentication, supervisor, heartbeat, remote actions |
| `botparty.tts` | TTS synthesis, audio playback |
