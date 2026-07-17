# Client architecture

`BotPartyClient` is the lifecycle orchestrator. Domain state lives in focused components:

| Component | Owns |
|---|---|
| `SafetyController` | stop latch, command epoch, cancellation permits |
| `GatewayConnection` | authenticated control socket, reconnect lifecycle |
| `DiagnosticsUploader` | redacted sequence buffer, upload acknowledgement |
| `UpdateManager` | signed A/B installation, activation and rollback markers |
| `CameraRuntime` | one camera owner, publisher task and restart state |
| hardware adapter | actuator writes, emergency stop and resource close |

The remaining lifecycle, media, command and operations mixins are migration boundaries. They contain orchestration against explicit components; new persistent state belongs in a component rather than another shared map.

## Ordering

1. Validate configuration and load the device key.
2. Claim the robot.
3. Start gateway, health and safety supervision.
4. Start media independently.
5. On shutdown: latch stop, close gateway/media, close the adapter, close HTTP.

The control plane remains available when media is degraded. No publisher restart may create a second owner for the same camera.
