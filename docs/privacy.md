# Privacy, data flows and retention

Every production promotion requires deployment-owned evidence matching
`compliance-evidence.example.json`. The protected release environment supplies the approved
record; the gate rejects placeholders, expired approval, a missing processor/region/retention
inventory, or failed opt-out, export and deletion tests. This is a technical gate, not legal
advice or a substitute for deployment-specific review.

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

## Deployment data flow

```text
Operator config/state ── claim + device key ──> BotParty API
Robot control <──────── robot auth token ─────> BotParty Gateway
Camera / optional audio ──────────────────────> LiveKit region selected by deployment
TTS text ── local engine OR approved cloud provider
Opt-in telemetry/diagnostics ─────────────────> BotParty API
Encrypted backup ─────────────────────────────> operator-selected storage
```

The local `/health` and optional `/metrics` endpoints bind to loopback by default. Non-loopback
access requires a private bearer-token file. Neither endpoint exports chat, command values, media,
user identifiers or secret values. The support bundle includes redacted config, checks, bounded
health/runtime information and an inventory manifest; it excludes media and message content.

## Go-live compliance record

Complete one row for every enabled destination. This is an operational evidence template, not a
legal determination.

| Function | Owner | Controller/processor roles | Purpose and legal basis | Region | Recipient/DPA | Retention | Export/delete test | Incident contact |
|---|---|---|---|---|---|---|---|---|
| BotParty identity/control | _required_ | _required_ | _required_ | _required_ | _required_ | _required_ | _date/result_ | _required_ |
| LiveKit video/audio | _required_ | _required_ | _required_ | _required_ | _required_ | _required_ | _date/result_ | _required_ |
| Cloud TTS, if enabled | _required_ | _required_ | _required_ | _required_ | _required_ | _required_ | _date/result_ | _required_ |
| Operational telemetry, if enabled | _required_ | _required_ | _required_ | _required_ | _required_ | _required_ | _date/result_ | _required_ |
| Product analytics, if enabled | _required_ | _required_ | _required_ | _required_ | _required_ | max 30 days | _date/result_ | _required_ |
| Diagnostics/support, if enabled | _required_ | _required_ | _required_ | _required_ | _required_ | _required_ | _date/result_ | _required_ |
| Backup storage | _required_ | _required_ | recovery | _required_ | _required_ | rotation policy | restore/delete drill | _required_ |

Before public use, record the applicable notice/consent experience for camera, microphone,
analytics and cloud TTS; document data-subject access, correction, export and deletion routing;
list subprocessors; set retention in each server service; test deletion in primary storage and
backup expiry; and assign breach notification and credential-revocation owners. Disabling a
feature must stop its outbound data path without weakening control or safety.

For device transfer or retirement: stop the service, revoke platform/provider credentials, remove
config and state, remove OTA/installer environments, expire all related backups under the rotation
policy, and record completion in the asset register. Flash media may require cryptographic erase or
physical destruction according to the operator's device policy.
