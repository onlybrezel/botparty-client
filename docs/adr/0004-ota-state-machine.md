# ADR 0004: Signed A/B OTA state machine

- Status: accepted
- Scope: update download, install, boot, confirm and rollback

## Context

An in-place update can leave the only environment unbootable. A signed artifact is insufficient if
platform, config, marker ownership or activation order is ambiguous.

## Decision

Manifest schema 2 binds Linux, architecture, version, exact size, SHA-256 and HTTPS URL under an
offline Ed25519 signature. The bundle installs with hashes into an inactive private slot. Version
and the actually loaded config path are validated before activation. `pending.json` records target,
previous, executable, digest and action ID. A boot-attempt without later control/media readiness
rolls back; only the interpreter inside the pending target can confirm.

## Invariants and failure modes

- Marker/state paths are owner-controlled, private and symlink-safe.
- Extraction rejects traversal, links, devices and expansion beyond the cap.
- Failed install never replaces the active slot; failed exec restores activation.
- Invalid marker state is fatal, no slot is exit 3, completed rollback is exit 4.
- Confirmation removes pending markers only after release identity matches.

OTA tests cover signature, target architecture, extraction, marker privacy, prepare/rollback exit
codes and previous-slot confinement. Release smoke tests build, install and validate the offline
bundle. Power-loss and storage fault injection remain part of platform release qualification.
