# Client architecture

The diagrams in [`architecture.md`](architecture.md) are the compact ownership and sequencing
reference.

`BotPartyClient` is the composition root. It does not inherit lifecycle behavior. Four explicit,
constructor-bound runtime components implement orchestration while domain state remains owned by
the root or by the focused services below:

| Runtime component | Responsibility |
|---|---|
| `ClientLifecycleComponent` | process lifecycle, health server, connection ownership and shutdown |
| `ClientMediaComponent` | camera registry, bitrate policy and media recovery |
| `ClientOpsComponent` | supervisor, telemetry, diagnostics, authentication and OTA operations |
| `ClientCommandsComponent` | command validation, TTS queue, safety latch and remote actions |

| Component | Owns |
|---|---|
| `SafetyController` | stop latch, command epoch, cancellation permits |
| `GatewayConnection` | authenticated control socket, reconnect lifecycle |
| `DiagnosticsUploader` | redacted sequence buffer, upload acknowledgement |
| `UpdateManager` | signed A/B installation, activation and rollback markers |
| `OutcomeOutbox` | durable idempotent command/action result delivery |
| `HardwareCommandQueue` | bounded latest-wins command ownership and cancellation |
| `RemoteActionExecutor` | scoped action preconditions, effects and postconditions |
| `CameraRuntime` | one camera owner, publisher task and restart state |
| hardware adapter | actuator writes, emergency stop and resource close |

`client_contract.py` only implements the generic binding. Each component declares its own narrow
host Protocol (`LifecycleHost`, `MediaHost`, `OperationsHost`, `CommandsHost`); foreign root state
is absent from that component's type. Cross-component calls pass through explicit port methods on
`BotPartyClient`; Python MRO order can no longer alter behavior. The legacy `*Mixin` names are
aliases for extension compatibility only and are not used by the production client. Mypy checks
the complete first-party package, so undeclared shared state fails CI.

## Ordering

1. Validate configuration and load the device key.
2. Start local health, safety supervision and the systemd watchdog.
3. Claim the robot and start the gateway.
4. Start media independently.
5. On shutdown: latch stop, close gateway/media, close the adapter, close HTTP.

The control plane remains available when media is degraded. No publisher restart may create a second owner for the same camera.
