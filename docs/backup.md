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

Restore verifies encryption, schema, allowed paths, total expanded size and custom-adapter syntax, stages files in their destination directories and preserves `*.before-restore` copies. A device that already has a different Device Key rejects the backup; restore onto a clean replacement or the original identity. Revoke the old device identity in the platform after loss or sale. Securely erase config, state, backup keys, OTA slots and service overrides before transferring hardware.
