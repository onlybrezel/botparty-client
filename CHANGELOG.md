# Changelog

This project follows Semantic Versioning and Keep a Changelog structure.

## [Unreleased]

### Added

- Latched command-epoch safety controller, precise motion deadlines and prioritized bounded dispatch.
- Signed streamer manifests and transactional signed A/B OTA updates with rollback.
- Strict transport/config/protocol validation, stable device state and capability manifests.
- Liveness/readiness/degraded health, metrics, diagnostics acknowledgement and TTS limits.
- Operator setup, config transfer, doctor, support bundle and encrypted backup/restore commands.
- Hardened service installer, reproducible release workflows, SBOM and dependency scanning.

### Changed

- Control starts independently of media and stops immediately on gateway loss.
- Camera ownership prevents duplicate publishers; server bitrate is a hard cap.
- Telemetry, product analytics and diagnostics are opt-in.
- Production installation uses a dedicated service user and immutable wheel environments.
- The main installer downloads the architecture-matched video streamer automatically from a
  release-pinned hash catalog; custom signed manifests remain supported.
- OTA uses a prefilled, disabled standard release channel and runs only after an authorized
  `update_client` action from the BotParty server.
- Production setup supports an automatic Git/bootstrap installer and a documented manual checkout;
  both paths install the verified streamer and the same hardened service.

### Removed

- Unsigned runtime streamer downloads, git-in-place updates and nonfunctional publish modes.
- Nonfunctional physical E-STOP pin option and unused compatibility modules.
