# ADR 0003: Direct publisher process boundary

- Status: accepted
- Scope: FFmpeg, libcamera, audio capture and native LiveKit publisher

## Context

Shell pipelines obscure producer failures, leak environment credentials and make process-group
cleanup unreliable. A direct H.264 publisher is still required for constrained devices.

## Decision

Pipelines use explicit argument arrays and independent process groups. Python connects producer and
consumer file descriptors directly and owns termination, escalation and wait. Only the native
publisher receives the LiveKit token; FFmpeg, libcamera and audio capture receive a minimized
environment. Bounded queues drop stale media rather than blocking control or safety. Frame progress,
not task existence alone, drives stall recovery.

## Invariants and failure modes

- No media command uses `shell=True`.
- Token/secret/password environment variables are removed from capture processes.
- A producer or publisher exit tears down the whole pipeline.
- Direct frame counters are monotonic across child restarts.
- Five failed recoveries mark the camera failed without stopping the control channel.

Publisher, camera-factory and process tests cover environment isolation, frame accounting,
on-demand ownership and stall recovery. Device FPS/CPU/temperature requires real performance
evidence.
