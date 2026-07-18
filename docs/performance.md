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

CI additionally runs `scripts/check-performance-budgets.py`. It installs the exact wheel and
hashed production lock in a clean virtual environment before measuring CLI startup. Reports bind
the wheel SHA-256, Python version, architecture and host; `--wheelhouse` makes dependency setup
strictly offline. Versioned ceilings are 2 MiB for the
wheel, 3 MiB for the sdist, 120 MiB for the offline OTA bundle, 40 production dependencies, 2.5 s
for cold `--help`/`--version`, and 3 s for cold example-config validation on the hosted amd64
runner. The JSON report preserves raw samples and p95. These host-independent packaging gates do
not replace real-device latency, temperature, memory or soak measurements.

No current report is fabricated from CI. Until one low-, one medium- and one reference-device
report is committed for a release, the corresponding physical-performance claims and moving
adapter support remain unverified.

## Long-running soak

Record the installed service process for at least 24 hours:

```bash
python scripts/run-soak.py --pid "$(systemctl show -p MainPID --value botparty-robot)" \
  --duration-hours 24 --device-class medium \
  --build-id "sha256-$(cut -d' ' -f1 /opt/botparty/installed-wheel.sha256)" \
  --raw-output soak.jsonl --report-output soak-report.json
python scripts/check-soak-report.py soak-report.json --raw soak.jsonl
```

The raw stream records RSS, file descriptors, threads, temperature, camera progress, reconnect
counters and bounded health snapshots.
The report binds the build and raw digest and rejects RSS growth above 1 MiB/hour, FD/thread drift
above 0.1/hour, device-class RSS, 85 °C, camera progress below 80%, collection gaps, fewer than 288
samples or a duration below 24 hours. Run the HIL
scenario's camera, TTS, command, reconnect, restart and power-loss phases during this window and
attest both files with the HIL evidence.
