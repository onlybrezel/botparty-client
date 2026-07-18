# BotParty Robot Client documentation

The client claims a robot identity, starts the control channel, and operates media as an independent subsystem. Hardware commands pass through a local safety latch before reaching an adapter.

```text
BotParty API ── claim/auth ── Robot client
Gateway ────── control ────── SafetyController ── Hardware adapter
LiveKit ────── media ──────── CameraRuntime
```

Start with [installation](installation.md), validate [configuration](configuration.md), then read the [security contract](security.md) before enabling movement.

## Reference

| Page | Contents |
|---|---|
| [Developer guide](../DEVELOPER_GUIDE.md) | local setup, module map, tests and debugging |
| [Installation](installation.md) | service user, signed streamer, health and recovery |
| [Configuration](configuration.md) | validated options and migrations |
| [Architecture](architecture.md) | trust boundaries, ownership and lifecycle diagrams |
| [Component contract](client-mixins.md) | runtime component ownership and lifecycle |
| [Architecture decisions](adr/index.md) | safety, acknowledgements, media and OTA invariants |
| [Multi-camera](multi-camera.md) | ownership, audio source and bitrate cap |
| [Protocol](protocol.md) | claim, commands and final acknowledgements |
| [Adapter support](adapter-support.md) | support levels and HIL evidence |
| [Security](security.md) | trust boundaries and controls |
| [Privacy](privacy.md) | data flows, defaults and retention |
| [Operations](operations.md) | SLOs, alerts and runbooks |
| [Release](release.md) | signed build, SBOM, OTA and rollback |
| [Backup](backup.md) | encrypted device recovery |
| [Performance](performance.md) | device-class budgets and measurement |
| [Troubleshooting](troubleshooting.md) | common operator failures |
