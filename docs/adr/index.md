# Architecture decisions

Runtime ownership, trust boundaries and lifecycle sequences are summarized in
[`../architecture.md`](../architecture.md).

| ADR | Decision |
|---|---|
| [0001](0001-safety-epoch-and-stop.md) | safety epochs, latch and stop confirmation |
| [0002](0002-command-acknowledgements.md) | command and TTS acknowledgement lifecycle |
| [0003](0003-direct-publisher.md) | direct media process and credential boundary |
| [0004](0004-ota-state-machine.md) | signed A/B OTA markers and confirmation |
| [0005](0005-native-artifact-trust.md) | native streamer artifact trust |

These decisions are normative for refactors. A change to an invariant requires an updated ADR,
tests and, for physical stop behavior, new HIL evidence.
