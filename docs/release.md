# Release and OTA process

1. Update version and `CHANGELOG.md` under SemVer.
2. Run Ruff, strict Mypy, tests, config validation, build, Twine check and dependency audit.
3. Build wheel and sdist from a clean, signed tag.
4. Generate CycloneDX SBOM, third-party notices and SHA-256 checksums.
5. Sign the immutable release manifest with the offline Ed25519 release key.
6. Publish artifacts without replacing an existing version.
7. Attach GitHub provenance attestation and verify the installed wheel entry point.
8. Roll out to a canary A/B slot, then confirm control and media readiness.

The release workflow accepts only annotated SSH-signed `v*` tags whose commit is an ancestor of
`origin/main`. The build job has read-only permissions and no persisted checkout credential; only
the protected `release` environment's publish job receives short-lived write/OIDC permissions.
GitHub Actions are pinned by commit SHA.

Provision `RELEASE_ALLOWED_SIGNERS` as a protected repository secret containing the OpenSSH
allowed-signers entries for release principals. The workflow writes it to an ephemeral 0600 file
and verifies the tag against it; a keyring inherited from the runner is never trusted. Provision
`OTA_ED25519_PRIVATE_KEY` only in the protected release environment. Rotate a release SSH key by
adding the new public principal, completing one overlap release, then removing the old principal.

The required protected-branch state is versioned in `.github/repository-policy.json`. A scheduled
workflow compares the live `main` protection via a read-only `REPOSITORY_POLICY_TOKEN`; missing
checks, unsigned commits, admin bypass, force pushes or deletions fail that job. Installer
integration, Quality, every supported-Python test job, package/audit and CodeQL are required before
merge. Repository settings remain an external GitHub control and must match the automated evidence
before release approval.

## OTA bundle

The release job builds the ZIP with `scripts/build-ota-bundle.py`. It contains the client wheel,
all production wheels, the exact temporary pip wheel and fully hashed runtime/installer
requirement files. Candidate creation pins pip from that wheelhouse and removes it after
validation; the active runtime environment contains no package installer.
`scripts/sign-ota-manifest.py` reads the
base64-encoded raw Ed25519 private key only from the protected release secret. Manifest schema 2
binds `version`, `platform`, `arch`, `bundleUrl`, `size` and `sha256`. Installation is offline into
the inactive slot. Config validation, installed-version match and entry-point smoke tests run before
atomic activation. A boot-attempt marker without later readiness confirmation triggers rollback.

OTA bundles are architecture-specific. Publish only architectures built and tested by the release matrix; an ARM bundle requires a matching ARM release runner and current HIL evidence.

The current workflow builds amd64, arm64 and armv7 bundles. ARM dependencies are resolved and the
offline install plus OTA transaction/rollback suite run on trusted native runners. The protected
publish environment receives the only OTA signing credential and signs all three immutable
bundles after their build jobs finish. The release also emits `BUILD_ID` as
`sha256-<wheel digest>`, checksums, a release-profile SBOM, notices, provenance and per-architecture
performance evidence. The production installer derives the identical build ID from that wheel.
Weekly supply-chain jobs audit every official hashed extras lock and publish per-profile
SBOM/license evidence; an unresolved result blocks promotion of that profile.

## Release gates

- `requirements/build-toolchain.txt` pins and hashes pip, setuptools, wheel and the build frontend;
  install it with `--require-hashes --no-deps`. `requirements/dev.txt` fixes the remaining Ruff,
  Mypy, test and audit tools. Builds run with `--no-isolation`, then the checkout is installed with
  `--no-deps -e .`.
- Coverage is collected with branches and XML/JSON/HTML artifacts. Risk-weighted module floors
  prevent regression in safety, auth, protocol, OTA, gateway, runtime, publisher and camera paths.
- Wheel, sdist, production dependency count, CLI cold starts and OTA bytes are checked against the
  versioned performance budgets; the JSON report is attached to the release.
- A moving adapter can be marked supported only when the release version has a schema-valid HIL
  report containing target hardware, firmware, raw-data digest, 24-hour soak and E-STOP p99.
- The generated adapter inventory must exactly match class capabilities, option schemas,
  dependencies and available HIL evidence.

Every tag is published as a prerelease. Production promotion is a separate protected workflow. It
downloads an attested `canary-report.json`, validates two distinct devices, control/media readiness,
safe stop, power-loss recovery, rollback and soak duration, and only then clears the prerelease
flag. A failed check stops promotion without changing the release.

The attested canary report uses schema version 1 and binds `releaseTag`, the full release `commit`,
the `sha256-...` build ID, a timezone-aware `observedAt` no older than seven days and an independent
reviewer. It contains at least two unique `deviceEvidenceId` records. Each device must pass
`controlReady`, `mediaReady`, `safeStopConfirmed`, `powerLossRecovered` and `rollbackConfirmed`,
run for at least two hours, and report command success and media availability of at least 99%,
stop p99 no greater than 150 ms and reconnect recovery p99 no greater than 60 seconds.

## Key rotation

Ship the next public key in a normal trusted release before signing with it. Keep the old key through one overlap window. Revoke a compromised key at the release host and disable OTA until a trusted base image installs a replacement.
