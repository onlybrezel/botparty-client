# Security and threat model

## Protected assets

- human and property safety around actuators;
- claim, robot-auth, cloud and backup credentials;
- control integrity and robot identity;
- native release and OTA execution;
- camera, microphone and diagnostic data.

## Trust boundaries

The browser, API, WebSocket gateway, LiveKit, release host, robot service and hardware buses are separate trust zones. Network input is bounded and validated before dispatch. Secrets are redacted from validation errors, logs, exports and support bundles.

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

The client requires robot-bound short-lived authentication, per-action IDs/timestamps, authorization for control, diagnostics and updates, and an E-STOP path that is not delayed by the normal command queue. The current companion backend enforces robot JWT binding, access policy, stale-control rejection, per-event rate limits and idempotent paid-command IDs. Fine-grained media/motion/speak/update scopes and immutable action auditing remain platform release gates.

## Device identity rotation

Stop the service, revoke the existing robot/device binding in the platform, create an encrypted
backup, remove only the local `device-key`, and restart to generate a new owner-only key. Re-claim
the robot with a short-lived claim token and verify the new binding before restoring control.
Never copy one active device key to two robots. Loss or sale requires server revocation before the
local config, state, OTA slots and backup material are erased.
