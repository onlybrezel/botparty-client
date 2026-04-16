# Client Mixins Architecture

`BotPartyClient` composes runtime behavior via four focused mixins:

- `ClientLifecycleMixin`: connect/disconnect lifecycle, claim/auth, room setup
- `ClientMediaMixin`: camera/audio pipelines and restart orchestration
- `ClientOpsMixin`: telemetry, supervisor, update/restart operations
- `ClientCommandsMixin`: command dispatch, safety stop, hardware execution

## Method resolution order (MRO)

`BotPartyClient(ClientLifecycleMixin, ClientMediaMixin, ClientOpsMixin, ClientCommandsMixin)`

Python MRO order:

1. `BotPartyClient`
2. `ClientLifecycleMixin`
3. `ClientMediaMixin`
4. `ClientOpsMixin`
5. `ClientCommandsMixin`

The mixins are intentionally stateful and share one client instance.
Common shared fields live in `BotPartyClient.__init__` and are created before any task starts.

## Shared state map

- Control path: `_hardware_lock`, `_hardware_safety_epoch`, `_latest_motion_command_id`
- Media path: `_camera_runtimes`, `_camera_restart_lock`, `_room`, `_livekit_connected`
- Gateway path: `_gateway`, `_gateway_task`, `_planned_reconnect_at`
- Ops path: `_http_session`, `_last_cpu_sample`, `_update_in_progress`

## Concurrency rules

- Hardware commands are serialized by `ClientCommandsMixin._run_hardware_command()` using `_hardware_lock`.
- Potentially blocking adapter methods (`on_command`, `emergency_stop`) are always executed via `asyncio.to_thread()`.
- Camera and gateway loops communicate only through shared client state, no cross-thread mutation.

## Reading guide

1. Start in `botparty_robot/client.py` for state initialization.
2. Read `client_runtime.py` for startup/reconnect behavior.
3. Read `client_commands.py` for real-time command safety semantics.
4. Read `client_media.py` and `camera.py` for publish pipeline details.
5. Read `client_ops.py` for telemetry/update/supervisor behavior.
