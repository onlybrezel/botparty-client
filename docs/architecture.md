# Architecture and trust boundaries

## Runtime ownership

```mermaid
flowchart LR
  Operator[Operator CLI and root-managed config] --> Root[BotPartyClient composition root]
  Root --> Life[Lifecycle and health]
  Root --> Media[Media coordinator]
  Root --> Ops[Operations supervisor]
  Root --> Control[Command coordinator]
  Control --> Safety[SafetyController and command epoch]
  Control --> Adapter[Hardware adapter]
  Control --> Outbox[Durable outcome outbox]
  Ops --> Gateway[GatewayConnection]
  Media --> Camera[One CameraRuntime per device]
  Camera --> Publisher[Camera or verified native publisher]
  Life --> Health[Loopback health and metrics]
```

The composition root owns cross-cutting lifecycle state. Safety, gateway, outcome, diagnostics,
update and camera services own their invariants. Hardware writes require an active command permit;
outcomes are persisted before network delivery; a camera never has two publisher owners.

## Process, credential and storage boundary

```mermaid
flowchart TB
  Release[Signed tag, attested offline bundle] -->|root install| Static[/root-owned /opt and /etc/]
  Static --> Service[Unprivileged botparty service]
  Service --> State[/service-owned /var/lib/botparty/]
  Service -->|bounded HTTPS/WSS| Platform[API and control gateway]
  Service -->|short-lived publish token| LiveKit[LiveKit]
  Service -->|fd-bound verified exec, minimal env| Native[Root-owned streamer]
  Service -->|permit-gated calls| Bus[GPIO, serial, USB, I2C, SPI]
  Cutoff[Hard-wired power cutoff] --> Bus
```

Static configuration, native code, launchers, units and trust anchors are read-only to the service.
Device identity, outbox, OTA markers and bounded runtime state are private to the service. Native
children receive only their required environment. Software stop complements, but never replaces,
the independent physical cutoff.

## Command lifecycle

```mermaid
stateDiagram-v2
  [*] --> accepted: validated and persisted
  accepted --> completed: observed effect
  accepted --> rejected: precondition or policy
  accepted --> failed: execution or delivery-safe fault
  accepted --> superseded: newer latest-wins command
  accepted --> cancelled_by_stop: safety stop invalidates epoch
  completed --> [*]
  rejected --> [*]
  failed --> [*]
  superseded --> [*]
  cancelled_by_stop --> [*]
```

Each subject has at most one `accepted` outcome and exactly one terminal state. Terminal transitions
are immutable. The backend deduplicates deterministic outcome IDs from reconnect delivery.

## Startup and shutdown

```mermaid
sequenceDiagram
  participant S as systemd
  participant C as Client
  participant H as Health/Safety supervisor
  participant P as Platform
  participant M as Media
  S->>C: start
  C->>C: validate config, identity and trust policy
  C->>H: start local liveness, watchdog and latched safety
  C-->>S: locally initialized; readiness remains degraded
  loop reconnecting authentication
    C->>P: claim and open control
  end
  C->>M: connect and publish track
  M-->>C: first new frame
  C-->>S: READY=1
  S->>C: stop
  C->>H: latch and confirm hardware stop
  C->>M: cancel and release publishers
  C->>P: close control and HTTP
  C->>C: close adapter; fail exit if stop is unconfirmed
```

## Installer and OTA transaction

```mermaid
flowchart LR
  Verify[Verify commit, bundle digest, hashes and signatures] --> Stage[Build complete staging tree]
  Stage --> Validate[Validate config, device policy and wheel]
  Validate --> Stop[Stop active service]
  Stop --> Swap[Atomic short activation]
  Swap --> Smoke[Installed artifact smoke and service activation]
  Smoke -->|success| Retain[Retain two rollback generations]
  Smoke -->|failure| Rollback[Restore exact previous release state]
```

OTA follows the same fail-closed shape with an inactive slot, signed architecture-bound manifest,
pending boot marker, readiness confirmation and automatic rollback.
