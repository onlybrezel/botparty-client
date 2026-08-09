# Changelog

This project follows Semantic Versioning and Keep a Changelog structure.

## [Unreleased]

### Added

- Durable command/action outcome outbox, explicit command lifecycle and observable remote-action
  postconditions.
- Offline hash-locked release bundle installation, native ARM OTA matrix, release evidence gates,
  canary promotion and standardized HIL/soak reports.
- First-frame readiness, latency SLO metrics, adapter behavior contracts, guided commissioning and
  versioned platform/operator schemas.

### Changed

- Production setup now uses one attested, version-bound installer that selects and verifies the
  release bundle for the host architecture.
- Runtime components now use explicit composition; command queues and remote actions own their
  state behind focused services.
- Operator diagnostics, runtime supervision and command dispatch now expose their major steps as
  small, named flows while preserving their public ordering and outcomes.
- Config export, schema generation and import share one atomic text-write path with cleanup on
  failure.
- Native executables are verified through fd-bound root-controlled trust anchors before every
  probe or execution.
- Offline installs pin their temporary pip frontend from the bundle and remove it from the
  validated runtime environment before activation.
- Locked dependencies remain installable across every supported Python version.
- Installer rollback preserves every untouched active component when activation stops early.
- The declared release scope is media-only until a moving adapter has current, attested HIL
  evidence.

### Removed

- The `espeak_loop` alias and the unconditional diagnostics-on-HTTP-400 compatibility retry.
- The unused `livekit-api` dependency and its otherwise unused protocol/JWT packages.
- The obsolete source-tree start wrapper; installed services continue to use the transactional
  service launcher.

## [0.2.0] - 2026-07-18

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
- Production setup uses the signed-commit bootstrap plus an attested, hash-bound offline bundle.

### Removed

- Unsigned runtime streamer downloads, git-in-place updates and nonfunctional publish modes.
- Nonfunctional physical E-STOP pin option, the `espeak_loop` alias and unused compatibility
  modules.

### Security and safety migration

- Moving adapters are fail-closed unless their capability metadata and current HIL evidence prove
  a confirmed safe-stop contract. Start the first boot with `hardware.type: none`.
- MQTT motion requires TLS, QoS acknowledgement and an acknowledgement-capable downstream design;
  serial motion requires framed command and device acknowledgements.
- OTA manifests use schema version 2 and bind platform and architecture. Re-sign old manifests.
- The bootstrap accepts only an immutable commit and a trusted SSH allowed-signers file.
- Profile options are closed and range-checked. Run `config validate` before restarting an upgraded
  service and move unsupported keys to a reviewed custom adapter only when required.

### Known limitations

- No moving adapter is advertised as supported without a version-bound HIL report.
