# ADR 0001: Safety epoch and confirmed stop

- Status: accepted
- Scope: command dispatch, adapters, disconnect, shutdown and update

## Context

Remote commands, worker threads and blocking device writes can overlap an emergency stop. Queue
ordering alone cannot prevent a stale worker from writing after a disconnect.

## Decision

`SafetyController` owns a monotonically increasing epoch and a fail-closed latch. Each actuator
command receives a permit for one epoch. Stop invalidates all permits before invoking the adapter
outside the latch lock. Motion is accepted only for an adapter that advertises verified safe stop.
Reset requires an authorized action and a confirmed prior stop.

## Invariants and failure modes

- Stop bypasses the normal queue and invalidates work synchronously.
- Old permits fail before or immediately after a guarded device write.
- Timeout, adapter exception or an earlier still-running stop leaves the client degraded and
  latched; it is never reported as success.
- A non-cancellable driver write cannot advertise confirmed safe stop based on software alone.
- MQTT, serial and NavQ remain motion-disabled until their full physical path has current HIL
  evidence.

Unit tests in `tests/test_safety.py` and command-pipeline tests cover epochs, blocking writes,
timeouts and cancellation. HIL reports must measure invocation-to-de-energized E-STOP p99 and the
24-hour soak contract in `reports/hil/README.md`.
