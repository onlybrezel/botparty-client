# Support

Run these commands before filing an issue:

```bash
botparty-robot --config config.yaml config validate
botparty-robot --config config.yaml doctor
botparty-robot --config config.yaml support-bundle --output support.zip
```

Attach the support ZIP, client version, device model, OS, adapter and exact failure time. The bundle is designed to exclude secrets, but review it before sharing. Do not post claim tokens, auth tokens, cloud credentials, backup keys, chat or media.

Use GitHub Issues for reproducible defects and documentation problems. Use the security channel in `SECURITY.md` for vulnerabilities. Community adapters are best-effort until their matrix entry is promoted to supported.
