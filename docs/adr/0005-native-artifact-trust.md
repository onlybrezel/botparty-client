# ADR 0005: Native streamer artifact trust

- Status: accepted
- Scope: installer and every native streamer execution

## Context

The native publisher executes outside Python packaging. A one-time download check does not detect
later local replacement or a writable digest sidecar.

## Decision

Official releases use an architecture-specific catalog that pins URL, byte length, version and
SHA-256. Private channels require an Ed25519-signed manifest and public key. Installation checks
HTTPS, size, digest, ELF platform and reported version before atomic activation. Every execution
rechecks the binary against an owner-controlled, non-group/world-writable SHA-256 sidecar.

## Invariants and failure modes

- No runtime download occurs during camera startup.
- Redirects remain credential-free HTTPS and downloads are size-bounded.
- Verification failure preserves the last working binary and fails the requested direct path.
- Binary and sidecar ownership/permissions are checked before execution.
- The publisher token is delivered only to the verified publisher process.

Artifact and video-profile tests cover tampering, manifest signature, architecture catalog,
sidecar permissions and pre-execution revalidation. Release checksums and provenance attest the
wheel; native binary releases retain their separate signed manifest.
