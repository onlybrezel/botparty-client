# Protocol v1

Production releases require deployment-owned staging evidence matching
[`platform-evidence.example.json`](platform-evidence.example.json). The gate binds the exact client
commit and generated contract to backend, gateway and LiveKit build IDs and requires negative
authorization, replay, rate-limit, token-revocation and recovery scenarios. The protected release
environment supplies the evidence; no production credentials are permitted in the test run.

Privileged wire models reject unknown fields and enforce message limits. Claim responses are limited to 128 KiB; WebSocket messages to 64 KiB.

Free JSON values are bounded before dispatch: maximum nesting depth 6, 512 nodes, 64 object keys,
128 list items and 4096 characters per string. Non-finite numbers, non-string keys and integers
outside the interoperable 53-bit range are rejected. Action-poll responses accept only `stream` and
at most 32 closed-schema `actions`.

Motion values are `null`, a speed from -100 through 100, or a closed `{x, y}` object with normalized
axes from -1 through 1. Speech objects accept only text, sender identity, anonymous flag and type.
Volume is limited to 0 through 100. Invalid values receive `invalid_command_value` before an
adapter or speech provider is called. The generated adapter inventory maps every declared command
to its value schema.

`control:command` requires `commandId`, `command`, `timestamp` and optional scalar `value`, `buttonId`, `metadata`, `userId`, `clientTimestamp`, `ackRequired`. The client rejects stale, future, replayed and malformed commands. Stop bypasses TTL and queue handling.

Final outcomes use `control:ack`:

```json
{"commandId":"uuid","status":"ACK"}
```

or:

```json
{"commandId":"uuid","status":"NACK","message":"hardware_error"}
```

Stable client error codes include `invalid_timestamp`, `invalid_command_value`, `replayed_action`,
`stale_action`, `command_queue_full`, `media_not_ready`, `safety_latched`, `cancelled_by_stop` and
`hardware_error`.

Speech queue admission may first be accepted. Its final result is `tts_played` or one of
`tts_queue_full`, `tts_empty`, `invalid_tts_volume`, `tts_disabled`, `tts_url_blocked`,
`tts_sender_blocked`, `tts_anonymous_blocked`, `tts_rate_limited`, `tts_budget_exhausted`,
`tts_cloud_consent_required`, `tts_timeout`, `tts_cancelled` and `tts_failed`. Result payloads never
echo speech text or sender data.

Remote actions require a unique `actionId` and accept only the version-1 action names documented by
the client. Unknown fields and action names are rejected. `durationSec` is limited to 10 through
900 seconds and is valid only for `set_log_stream`. Actions require a least-privilege scope:
`media:restart`, `control:restart`,
`safety:reset`, `speak:restart`, `update:install` or `diagnostics:read` according to action type.
Outcomes use `robot:action-result` with `accepted`, `completed`, `rejected` or `failed`, a stable
code and an occurrence timestamp. Repeated action IDs receive `replayed_action`.

Every command/action outcome carries a deterministic `outcomeId`. A successful WebSocket write is
not delivery confirmation. The gateway persists the outcome idempotently and returns
`robot:outcome-ack` with the same 64-character `outcomeId`; only then may the client remove the
durable outbox entry. Unconfirmed entries are resent after reconnect or process restart.

Robot claim uses a short-lived robot-bound auth token. Capability manifests are deterministic and hashed. `server.report_capabilities_in_claim` remains off until the target API explicitly accepts the field; local health always exposes the manifest.
