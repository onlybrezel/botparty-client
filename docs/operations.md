# Operations, SLOs and runbooks

## Build identity

Health, telemetry and the startup log report the client version and build ID. The installer derives
`BOTPARTY_BUILD_ID` from the installed wheel SHA-256 and writes it into the root-owned unit. Release
artifacts include the same value in `BUILD_ID`; the wheel is covered by checksums and provenance
attestation. `version-<client-version>` is used only outside the production installer.

## Service indicators

| SLI | Target |
|---|---|
| control availability | 99.9% monthly per fleet, excluding planned maintenance |
| local E-STOP dispatch | p99 below configured `stop_timeout_ms`; HIL budget in performance matrix |
| control reconnect | p95 below 15 s after network recovery |
| media readiness | 99% for camera-enabled robots |
| OTA success | 99% without manual recovery; 100% safe rollback on failed readiness |

Alert on sustained states: control disconnected for 30 s, five camera restarts in a session, repeated watchdog stops, OTA rollback, readiness failure for 5 min, memory above the device budget for 15 min. Do not use chat, video, user IDs or command values as metric labels.

Latency SLOs use cardinality-free client gauges: claim p95 below 5 s, control reconnect p95 below
15 s, medium-device first frame/media restart p95 below 12 s, command execution p99 below 150 ms
and confirmed stop p99 below 100 ms. Outcome backlog must remain below 60 seconds and 100 pending
entries. The shipped Prometheus rules alert on sustained violations.

Set `BOTPARTY_METRICS_ENABLED=1` to expose `/metrics` on the same bind address and under the same
authentication policy as health. The endpoint is off by default and contains only fixed,
low-cardinality counters. A non-loopback bind still requires
`BOTPARTY_HEALTH_AUTH_TOKEN_FILE`. Prometheus examples are in
[`monitoring/prometheus-alerts.yml`](monitoring/prometheus-alerts.yml).

## Control loss

1. Confirm `/live`; expect `/ready` to be 503.
2. Keep the safety latch active.
3. Check DNS, TLS time, gateway reachability and `controlDisconnects`.
4. Restore control; perform an authorized safety reset only with the area clear.

## Media failure

1. Confirm control remains connected and E-STOP remains available.
2. Inspect per-camera state, last-frame age and restart count in `/health`.
3. Check device ownership and run `doctor`.
4. Do not restart while an old publisher still owns the device.

## Repeated safety stops

Keep the robot latched. Inspect command age, queue drops, gateway loss and adapter logs. Test the hard-wired cutoff. Do not reset remotely until the cause and physical area are verified.

## Update failure

Keep actuators stopped. Let the launcher restore the previous slot. Validate config and signed bundle metadata, then collect a support bundle. Never repair a production checkout with `git reset` or an in-place dependency install.

`ota prepare` returns 3 when no managed slot exists, 4 after a completed automatic rollback and 1
for invalid state. The launcher treats invalid state as fatal. A normal installer upgrade uses the
separate `venv.previous` environment; with the service stopped, run
`sudo /opt/botparty/botparty-service-launcher.sh --rollback-installer`. Existing older previous
environments are archived by timestamp during installation and should be removed only after the
site's rollback retention window expires.

## Resource pressure

Lower secondary-camera FPS/resolution first, then the primary target. The server bitrate is a cap. If health remains degraded, disable media while retaining control rather than weakening safety supervision.

## Operator message contract

Protocol failures use stable English codes such as `stale_action`, `missing_scope` and
`hardware_error`; display text is not part of the machine contract. Human-readable messages may be
localized independently; codes, JSON field names and metric names remain English and stable.

## Commissioning

Run the offline, non-moving commissioning check before starting the service:

```bash
botparty-robot --config /etc/botparty/config.yaml commission \
  --output /var/lib/botparty/commission-report.json
```

For the platform check, configure `hardware.type: none`, connect the camera and run
`commission --online`. The command waits for control connectivity and, when media is configured,
the first frame. It never sends a hardware command. The JSON report keeps separate `host`,
`service`, `data_processing`, `motion_guard`, `claim`, `control` and `media` phases with stable
result codes. Offline runs mark platform phases as skipped instead of presenting them as passed.

## Latency and delivery indicators

The endpoint exports cardinality-free p95/p99 gauges for claim, command receipt, command execution
and confirmed stop latency, plus pending-outcome count and oldest-outcome age. No robot, user,
command or media identifiers are labels. Device-class SLOs and release budgets are defined in
`docs/performance.md`; the example rules alert on sustained backlog and latency burns.

## Operational release evidence

Production promotion requires a deployment-owned operations record matching
[`operations-evidence.example.json`](operations-evidence.example.json). It records a delivered test
alert, current offsite backup, and independently reviewed restore drill within the deployment's RPO
and RTO. The protected release environment supplies this record; placeholder evidence fails the
release gate.
