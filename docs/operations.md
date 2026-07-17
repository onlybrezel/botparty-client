# Operations, SLOs and runbooks

## Build identity

Health, telemetry and the startup log report the client version and build ID. Release
deployments should set `BOTPARTY_BUILD_ID` to the immutable release or provenance ID. The
validated fallback is `version-<client-version>`.

## Service indicators

| SLI | Target |
|---|---|
| control availability | 99.9% monthly per fleet, excluding planned maintenance |
| local E-STOP dispatch | p99 below configured `stop_timeout_ms`; HIL budget in performance matrix |
| control reconnect | p95 below 15 s after network recovery |
| media readiness | 99% for camera-enabled robots |
| OTA success | 99% without manual recovery; 100% safe rollback on failed readiness |

Alert on sustained states: control disconnected for 30 s, five camera restarts in a session, repeated watchdog stops, OTA rollback, readiness failure for 5 min, memory above the device budget for 15 min. Do not use chat, video, user IDs or command values as metric labels.

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

## Resource pressure

Lower secondary-camera FPS/resolution first, then the primary target. The server bitrate is a cap. If health remains degraded, disable media while retaining control rather than weakening safety supervision.

## Operator message contract

Protocol failures use stable English codes such as `stale_action`, `missing_scope` and
`hardware_error`; display text is not part of the machine contract. The current operator language
is English. Translation infrastructure will be added only with a reviewed target language, while
codes, JSON field names and metric names remain unchanged.
