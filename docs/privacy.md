# Privacy, data flows and retention

| Data | Destination | Default | Local retention | Purpose |
|---|---|---|---|---|
| claim token, device key | BotParty API | required | config/state until rotation | bind robot identity |
| robot auth token | API and gateway | required after claim | memory only | authenticated operations |
| camera/microphone | LiveKit | camera configured; audio explicit | none in client | live robot session |
| chat/TTS text | selected local/cloud TTS | TTS off | processing lifetime | speech output |
| operational metrics | BotParty API | off | memory counters | fleet health |
| product milestones | BotParty API | off | process lifetime | activation/support funnel |
| diagnostic lines | BotParty API | off and time-limited | 15 minutes by default | operator support |
| support bundle | operator-selected path | manual | operator-controlled | offline support |

Product milestones are fixed names: `install_validated`, `claimed`, `control_ready`, `first_media`,
`first_command_ack` and `reboot_healthy`. They contain no chat, media, username, command value, path
or secret. Diagnostic upload requires both local opt-in and a time-limited, scoped remote action;
redaction runs before buffering. Operators may add literal deployment-specific redaction terms.

Cloud TTS requires `cloud_data_processing_accepted: true` in engine options. Provider credentials should use secret files. URL filtering, anonymous denial, sender limits, daily character budgets and operation timeouts are enabled by default.

Operators must publish the applicable controller/processor roles, legal basis, regional endpoints, server retention, deletion and data-subject request process for their deployment. The client supplies technical minimization and deletion controls; it cannot determine those legal terms.

For the platform analytics deployment, retain optional product milestones for at most 30 days and
show funnel aggregates only for cohorts of at least ten robots. Operational telemetry and support
diagnostics use separate access controls and retention. Disabling product analytics must not alter
control, safety, health or operational telemetry behavior.
