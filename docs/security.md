# Security and threat model

## Protected assets

- human and property safety around actuators;
- claim, robot-auth, cloud and backup credentials;
- control integrity and robot identity;
- native release and OTA execution;
- camera, microphone and diagnostic data.

## Trust boundaries

The browser, API, WebSocket gateway, LiveKit, release host, robot service and hardware buses are separate trust zones. Network input is bounded and validated before dispatch. Secrets are redacted from validation errors, logs, exports and support bundles.
See [`architecture.md`](architecture.md) for the process, credential and storage boundary.

## Controls

- Local `SafetyController` with epoch permits, interruptible waits and a latched stop.
- Stop on control loss, deadline, update, shutdown and explicit E-STOP.
- Command IDs, timestamps, bounded replay cache and bounded priority queue.
- HTTPS/WSS enforcement; loopback-only insecure development mode.
- Private, atomic device keys with owner, mode and symlink checks.
- A release-pinned SHA-256 catalog for the official streamer; custom native release channels
  require signed Ed25519 manifests. Size, platform and architecture are always verified.
- Immutable A/B OTA slots, offline hashed wheel install and readiness rollback.
- Dedicated service user, explicit device groups and systemd isolation.
- Opt-in diagnostics and telemetry with fixed schemas and no content labels.

## Physical safety

Software E-STOP does not replace a normally closed, hard-wired power cutoff. Validate stop direction and latency on the exact driver, power stage, load and firmware used in production. Community adapters carry no release safety guarantee until their HIL row is promoted.

## Server contract

The client requires robot-bound short-lived authentication, per-action IDs/timestamps,
authorization for control, diagnostics and updates, and an E-STOP path that is not delayed by the
normal command queue. These backend properties are not inferred from client code. Every release
must carry the protected, attested platform test record described in
[`protocol.md`](protocol.md), including tenant isolation, token rejection, scope, rate-limit,
idempotency, media-room and audit scenarios.

## Device identity rotation

The current platform contract does not expose an authenticated revoke-and-rebind operation to this
client. Do not remove or replace `device-key` as a local shortcut: the client cannot prove that the
old binding was revoked, and an interrupted handover could leave two identities or lock out the
device. Keep the service stopped and the physical actuator cutoff open while an operator revokes
the old binding in the platform. Rotation remains blocked until the backend provides an atomic,
audited revocation and re-claim contract that the CLI can verify. Never copy one active device key
to two robots. Loss or sale requires platform revocation before local config, state, OTA slots and
backup material are erased.
