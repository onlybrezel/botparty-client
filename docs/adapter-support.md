# Adapter support matrix

`supported` requires current contract tests and a passing HIL report for the release. `community` means the integration is available but carries no production safety guarantee. `experimental` may change. `deprecated` is removal-bound.

| Adapter | Interface | Stop | Close | Level | Current HIL evidence |
|---|---|---|---|---|---|
| none | no device | no actuators | yes | supported | CI contract |
| adafruit_pwm | I2C/PCA9685 | neutral PWM | de-energize | community | none |
| l298n | GPIO | all pins low | GPIO cleanup | community | none |
| motor_hat | I2C | release motors | release | community | none |
| mdd10 | GPIO/PWM | 0% duty | PWM/GPIO cleanup | community | none |
| motozero | GPIO | all pins low | GPIO cleanup | community | none |
| pololu | driver SDK | zero speed | zero speed | community | none |
| mc33926 | driver SDK | zero/disable | disable | community | none |
| thunderborg | I2C SDK | zero motors | zero motors | community | none |
| gopigo2, gopigo3 | vendor SDK | stop | stop | community | none |
| megapi_board | serial SDK | all ports zero | SDK close when available | community | none |
| maestro_servo | serial SDK | neutral targets | controller close | community | none |
| serial_board, telly | serial | configured stop command | serial close | community | none |
| mqtt_pub | MQTT | configured stop message | disconnect | community | none |
| navq | MAVSDK | zero thrust | async stop | experimental | none |
| owi_arm | USB | zero transfer | USB dispose | community | none |
| cozmo, vector | vendor SDK | vendor motor stop | connection close | community | none |
| max7219 | SPI display | display off | SPI close | community | none |
| custom | local Python | custom contract | custom close | experimental | operator-owned |

Every moving adapter uses command-epoch checks around active writes and interruptible waits where the SDK permits them. Async/vendor firmware can add latency outside Python; measure it on the exact device before production.

Video profiles and TTS engines are capability-reported separately. Direct native video binaries must pass signed artifact verification. Cloud TTS is experimental until the deployment records provider consent, limits and regional handling.

The authoritative generated inventory is
[`generated/adapter-inventory.json`](generated/adapter-inventory.json). CI compares it semantically
with registries, option JSON schemas, Python dependencies and HIL reports; hand-edited tables cannot
silently override adapter metadata.
