# Developer guide

Start here for local development, module ownership and verification commands. Operator-facing
installation, configuration and recovery are documented in [`docs/`](docs/index.md).

## What this repository contains

The repository builds one Python edge application for Linux robots. It connects to the BotParty
HTTP/control endpoints and LiveKit, owns local camera and hardware resources, and runs as one
unprivileged systemd service.

It does not contain the BotParty web frontend, platform API, database, tenant or role model,
Redis, a worker service, or a production Compose stack. The Dockerfile under `tests/installer/`
is an isolated integration environment for the privileged installer. Platform-side changes and
multi-instance API behavior must be tested in their own repositories.

## Local setup

Python 3.10 through 3.14 is supported. The lockfiles are generated with Python 3.12.

```bash
python3 -m venv .venv
.venv/bin/pip install --require-hashes --no-deps -r requirements/build-toolchain.txt
.venv/bin/pip install --require-hashes -r requirements/dev.txt
.venv/bin/pip install --no-deps -e .
BOTPARTY_CLAIM_TOKEN=test-claim-token \
  .venv/bin/botparty-robot --config config.example.yaml config validate
```

Use an extra-specific lockfile when working with optional hardware or media dependencies, for
example `requirements/vision.txt`, `requirements/hardware.txt`, or `requirements/google-tts.txt`.
Do not install an unlocked extra in release verification.

## Module map

| Area | Entry modules | Responsibility |
|---|---|---|
| Composition | `client.py`, `client_contract.py` | owns runtime state and binds four typed components |
| Lifecycle | `client_runtime.py` | startup, shutdown, health, session and connection ownership |
| Control | `client_commands.py`, `command_queue.py` | command validation, acknowledgement, TTS dispatch and hardware queue |
| Operations | `client_ops.py`, `remote_actions.py` | supervision, authentication, diagnostics, telemetry and scoped remote actions |
| Media | `client_media.py`, `camera.py`, `publisher.py` | camera ownership, LiveKit publication and recovery |
| Safety | `safety.py`, `hardware/base.py` | stop latch, epochs, permits and guarded actuator writes |
| Delivery | `gateway.py`, `outbox.py`, `protocol.py` | control transport, durable outcomes and wire models |
| Configuration | `config.py`, `profile_options.py`, `device_state.py` | closed schemas, profile options and private state paths |
| Updates | `ota.py`, `artifacts.py`, `process_group.py` | signed A/B update state and verified native execution |
| Operator tools | `__main__.py`, `operator.py`, `backup.py` | CLI, doctor, portable config, support bundle and encrypted backup |
| Extensions | `hardware/`, `video/`, `tts/` | concrete device, publisher and speech profiles |

The detailed ownership diagram is in [`docs/architecture.md`](docs/architecture.md). Important
invariants are recorded in [`docs/adr/`](docs/adr/index.md).

## Runtime flows

### Command

1. `GatewayConnection` authenticates the control socket and parses the event envelope.
2. `ClientCommandsComponent._validate_command` checks timestamp, replay ID, TTL, value schema,
   adapter release status, safe-stop evidence, safety latch and media policy in that order.
3. Chat and TTS commands terminate in their dedicated queues. Hardware commands enter the bounded
   `HardwareCommandQueue`; newer motion supersedes older pending motion.
4. `_run_hardware_command` obtains a permit from `SafetyController` immediately before the adapter
   write. A stop invalidates all older permits.
5. Accepted and terminal outcomes are persisted in `OutcomeOutbox` before delivery. Reconnects
   drain the same deterministic outcomes until the platform acknowledges them.

`stop` deliberately bypasses the normal validation and command queue. Do not move it behind a
queue, media precondition, or network-dependent operation.

### Media

`ClientMediaComponent` creates one `CameraRuntime` for each enabled camera. A runtime owns exactly
one publisher task. First-frame progress establishes readiness; the operations supervisor restarts
a finished or stalled owner without creating a second publisher.

### Startup and shutdown

`BotPartyClient` is the composition root. `ClientLifecycleComponent` starts local health and
supervision, then control and media. Shutdown first latches and confirms hardware stop, then closes
network/media resources and the adapter. An unconfirmed hardware stop remains a failing shutdown.

## Configuration and environment

The YAML schema in `config.py` and `profile_options.py` is authoritative. Keep
`config.example.yaml`, `docs/configuration.md`, generated contracts and tests synchronized with
schema changes.

The main operator variables are:

| Variable | Purpose |
|---|---|
| `BOTPARTY_CONFIG` | default config path when `--config` is absent |
| `BOTPARTY_CLAIM_TOKEN` | secret claim-token override used during provisioning/import |
| `BOTPARTY_STATE_DIR` | private identity, outbox and runtime state directory |
| `BOTPARTY_LOG_LEVEL` | standard Python log level; defaults to `INFO` |
| `BOTPARTY_LOCALE` | CLI locale, `en` or `de` |
| `BOTPARTY_HEALTH_ENABLED` | enable the local health server |
| `BOTPARTY_HEALTH_HOST` / `BOTPARTY_HEALTH_PORT` | health listener address and port |
| `BOTPARTY_HEALTH_AUTH_TOKEN_FILE` | required for a non-loopback health listener |
| `BOTPARTY_METRICS_ENABLED` | expose metrics through the local health server |
| `BOTPARTY_OTA_MANIFEST_URL` | private-channel OTA manifest override |
| `BOTPARTY_OTA_PUBLIC_KEY_FILE` | OTA trust-anchor override |
| `BOTPARTY_OTA_STATE_DIR` | OTA slot/state override |

Installer and release variables are documented in `docs/installation.md` and `docs/release.md`.
Test-only failure injection variables stay inside `tests/installer/run.sh`.

Legacy config translation remains only through its published deadline, 2026-09-01. Do not add new
legacy branches. Removal after the deadline requires a changelog entry and migration tests.

## Change workflow

Run the smallest relevant tests after each edit, then the complete gate before handoff:

```bash
.venv/bin/ruff format .
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/pytest -q
.venv/bin/python scripts/check-secrets.py
.venv/bin/python scripts/check-doc-links.py
.venv/bin/python scripts/check-repository-layout.py
.venv/bin/python scripts/adapter-inventory.py --check
.venv/bin/python scripts/generate-platform-contract.py --check
.venv/bin/python scripts/check-legacy-deadline.py
.venv/bin/python scripts/check-hil-reports.py --release-profile release-profile.json
shellcheck scripts/*.sh tests/installer/*.sh
bash -n scripts/*.sh tests/installer/*.sh
git diff --check
```

Coverage is a separate gate. Put generated reports outside the checkout for local runs:

```bash
COVERAGE_FILE=/tmp/botparty.coverage \
  .venv/bin/pytest -q --cov=botparty_robot --cov-branch \
  --cov-report=json:/tmp/botparty-coverage.json
.venv/bin/python scripts/check-coverage.py /tmp/botparty-coverage.json
```

Build and smoke-test the distribution:

```bash
rm -rf dist build
.venv/bin/python -m build --no-isolation
.venv/bin/twine check dist/*
.venv/bin/pip-audit -r requirements/production.txt
```

The `rm` command above is limited to generated package output. Never use broad cleanup commands in
a working tree with uncommitted changes.

## Installer integration test

Docker is used only for the privileged installer transaction:

```bash
docker build -f tests/installer/Dockerfile -t botparty-installer-test .
docker run --rm botparty-installer-test
```

The test covers install, upgrade, rollback, failure injection, policy rejection and the generated
systemd unit. It is not a production image and does not model the external BotParty platform.

## Extending profiles safely

- Add hardware behavior to one adapter under `hardware/`; keep permit validation in the base
  class and implement an idempotent, bounded `emergency_stop`.
- Register closed options in `profile_options.py`; do not accept arbitrary keys for built-ins.
- Update adapter inventory and HIL evidence. Motion remains unreleased without current evidence.
- Keep video process creation in the existing verified-executable/process-group boundary.
- Keep cloud TTS credentials outside YAML and require the existing data-processing consent.
- Test observable behavior and failure paths. Use characterization tests before changing safety,
  replay, transaction, update, or process-lifecycle semantics.

## Debugging map

- Configuration/startup failures: `__main__.py`, `config.py`, `device_state.py`
- Control reconnects or protocol errors: `gateway.py`, `protocol.py`
- Rejected or missing command outcomes: `client_commands.py`, `outbox.py`
- Safety latch or stopped motion: `safety.py`, selected `hardware/` adapter
- Camera restart/readiness: `client_media.py`, `client_ops.py`, `camera.py`, `publisher.py`
- OTA activation/rollback: `ota.py`, then `docs/operations.md`
- Native child termination: `process_group.py`, `artifacts.py`

Errors should be logged once at a system boundary. Add safe fault codes to `faults.py` rather than
passing exception text into health or remote responses.
