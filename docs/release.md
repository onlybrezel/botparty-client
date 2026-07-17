# Release and OTA process

1. Update version and `CHANGELOG.md` under SemVer.
2. Run Ruff, strict Mypy, tests, config validation, build, Twine check and dependency audit.
3. Build wheel and sdist from a clean, signed tag.
4. Generate CycloneDX SBOM, third-party notices and SHA-256 checksums.
5. Sign the immutable release manifest with the offline Ed25519 release key.
6. Publish artifacts without replacing an existing version.
7. Attach GitHub provenance attestation and verify the installed wheel entry point.
8. Roll out to a canary A/B slot, then confirm control and media readiness.

The release workflow accepts only signed `v*` tags. GitHub Actions are pinned by commit SHA and use minimal permissions. Branch protection must require `quality`, all supported-Python `test` jobs, `package` and CodeQL before merge.

## OTA bundle

The release job builds the ZIP with `scripts/build-ota-bundle.py`. It contains the client wheel, all production wheels and a fully hashed `requirements.txt`. `scripts/sign-ota-manifest.py` reads the base64-encoded raw Ed25519 private key only from the protected `OTA_ED25519_PRIVATE_KEY` release secret. The signed manifest contains `version`, `bundleUrl`, `size` and `sha256`. Installation is offline into the inactive slot. Config validation and entry-point smoke tests run before atomic activation. A boot-attempt marker without later readiness confirmation triggers rollback.

OTA bundles are architecture-specific. Publish only architectures built and tested by the release matrix; an ARM bundle requires a matching ARM release runner and current HIL evidence.

## Key rotation

Ship the next public key in a normal trusted release before signing with it. Keep the old key through one overlap window. Revoke a compromised key at the release host and disable OTA until a trusted base image installs a replacement.
