# Performance and reliability budgets

Run `python -m pytest tests/test_safety.py` for deterministic software latency and use the HIL report template below on real hardware.

| Class | Example | control-ready p95 | command p99 | E-STOP p99 | RSS steady | shutdown |
|---|---|---:|---:|---:|---:|---:|
| low | Pi 3 / armv7 | 20 s | 250 ms | 150 ms | 350 MiB | 8 s |
| medium | Pi 4 / arm64 | 12 s | 150 ms | 100 ms | 450 MiB | 6 s |
| reference | Pi 5 / amd64 | 8 s | 100 ms | 75 ms | 550 MiB | 5 s |

E-STOP is measured from local stop invocation to confirmed de-energized output with an oscilloscope or logic analyzer. A supported adapter release must stay below both its row and configured timeout. Record p50/p95/p99, CPU, RSS, temperature, bitrate, first frame, reconnect and 24-hour RSS slope with OS, kernel, Python, camera, encoder, driver and client version.

Legacy RGBA publishing is the compatibility path. Use direct H.264 on constrained systems. The effective bitrate is the lower of local target and server cap. If CPU, temperature or frame-age budgets fail, reduce resolution/FPS and report degraded state; control and safety tasks retain priority.

Real-device numbers are release evidence, not values inferred from CI. Store reports under `reports/hil/<version>/<device>.json`; promote an adapter to `supported` only when the current release has a passing report.

Create a report template with `python scripts/check-hil-reports.py --template medium`. The report
validator enforces the table budgets, a 24-hour soak and a maximum RSS slope of 1 MiB/hour. CI
validates all committed reports. Adapter metadata is available as JSON from
`python scripts/adapter-inventory.py`.
