# Protocol v1

Privileged wire models reject unknown fields and enforce message limits. Claim responses are limited to 128 KiB; WebSocket messages to 64 KiB.

`control:command` requires `commandId`, `command`, `timestamp` and optional scalar `value`, `buttonId`, `metadata`, `userId`, `clientTimestamp`, `ackRequired`. The client rejects stale, future, replayed and malformed commands. Stop bypasses TTL and queue handling.

Final outcomes use `control:ack`:

```json
{"commandId":"uuid","status":"ACK"}
```

or:

```json
{"commandId":"uuid","status":"NACK","message":"hardware_error"}
```

Stable client error codes include `invalid_timestamp`, `replayed_action`, `stale_action`, `command_queue_full`, `media_not_ready`, `safety_latched`, `cancelled_by_stop` and `hardware_error`.

Remote actions require a unique `actionId` and accept only the version-1 action names documented by
the client. Unknown fields and action names are rejected. `durationSec` is limited to 10 through
900 seconds and is valid only for `set_log_stream`. Actions require a least-privilege scope:
`media:restart`, `control:restart`,
`safety:reset`, `speak:restart`, `update:install` or `diagnostics:read` according to action type.
Outcomes use `robot:action-result` with `accepted`, `completed`, `rejected` or `failed`, a stable
code and an occurrence timestamp. Repeated action IDs receive `replayed_action`.

Robot claim uses a short-lived robot-bound auth token. Capability manifests are deterministic and hashed. `server.report_capabilities_in_claim` remains off until the target API explicitly accepts the field; local health always exposes the manifest.
