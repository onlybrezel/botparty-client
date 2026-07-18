# Backup and restore

Generate an operator-held encryption key and create a backup:

```bash
botparty-robot backup generate-key --key-file /secure/botparty-backup.key
botparty-robot --config /etc/botparty/config.yaml backup create \
  --key-file /secure/botparty-backup.key --output robot-state.bpb
```

The authenticated AES-256-GCM archive contains the config, device key, schema/client metadata and an optional adjacent custom adapter. Archive and key must be stored separately. Both are mode 0600.

Restore only with the service stopped:

```bash
botparty-robot --config /etc/botparty/config.yaml backup restore \
  --key-file /secure/botparty-backup.key --input robot-state.bpb
botparty-robot --config /etc/botparty/config.yaml config validate
```

Restore verifies encryption, schema, allowed paths, total expanded size and custom-adapter syntax,
stages files in their destination directories and preserves `*.before-restore` copies. Existing
owner/group/mode policy is retained. On a new production host, the config directory must already be
`root:botparty 0750` and the state directory `botparty:botparty 0700`; restored static config and
custom code become root-owned while the Device Key remains service-owned. A device that already has
a different Device Key rejects the backup; restore onto a clean replacement or the original
identity. Revoke the old device identity in the platform after loss or sale. Securely erase config,
state, backup keys, OTA slots and service overrides before transferring hardware.

## Operating contract

- RPO: create a backup after every identity, hardware, provider or config change and at least every
  seven days. Alert when the newest verified copy is older than seven days.
- RTO: keep a compatible replacement host and target a validated restore within four hours.
- Rotation: retain the latest seven daily, four weekly and twelve monthly encrypted archives.
  Remove expired copies with the storage provider's verified deletion procedure.
- Separation: keep the AES key in a different account or offline safe from the archives. Give the
  service user no access to the operator backup key.
- Offsite: store at least one encrypted copy outside the robot's site. Record archive SHA-256,
  client version, creation time, owner and retention class in the backup inventory.
- Rotation of credentials: after a lost device or exposed archive, revoke its platform identity,
  rotate claim/provider credentials and create a fresh backup.

## Quarterly restore drill

1. Select a non-production replacement with motors disconnected and stop the client service.
2. Verify archive age and SHA-256 against the inventory; copy archive and key through separate
   channels.
3. Restore, run `config validate` and `doctor`, and confirm that the restored device identity is the
   intended one. Do not connect both original and restored identities simultaneously.
4. Start with `hardware.type: none`; verify control, media and TTS without actuator output.
5. Record archive ID, operator, start/end time, validation output and any recovery gap. Delete the
   drill copy and temporary key material through the storage medium's documented process.

The application checks plaintext and ciphertext limits separately. Config, device key and adjacent
custom adapter must be private regular files owned by the invoking service account; symlinks and
group/world-readable sources are rejected.
