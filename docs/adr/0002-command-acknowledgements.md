# ADR 0002: Command acknowledgement lifecycle

- Status: accepted
- Scope: WebSocket commands, remote actions and TTS

## Context

Transport receipt is not execution. Immediate success acknowledgements hid queue overflow, safety
rejection, provider failure and incomplete updates.

## Decision

Every command ID has stable accepted and final outcomes. Queue admission may emit `accepted`; only
actual adapter completion emits `completed`. Rejection codes are stable English identifiers and do
not contain command values. TTS retains command metadata through its bounded queue and emits
`tts_played` only after playback; filtering, budget, queue, timeout and provider failures emit their
specific final rejection. OTA actions remain active until readiness confirmation after reboot.

## Invariants and failure modes

- Replay and stale IDs are rejected before work starts.
- Queue full and invalid volume never become completed.
- Safety cancellation is `cancelled_by_stop`; an adapter exception is `hardware_error`.
- A repeated remote action has one deterministic final rejection.
- Text, sender data and provider messages are absent from result payloads.

Contract coverage lives in command, gateway, TTS and update tests. The protocol reference lists the
machine states and codes; UI text is not part of that contract.
