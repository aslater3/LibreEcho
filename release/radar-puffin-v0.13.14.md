# LibreEcho radar-puffin v0.13.14

LibreEcho 0.13.14 is a focused feature-provenance and OTA v2 maintenance
release for the Amazon Echo 2nd Gen (`radar_puffin`, ARMv7, Linux 6.1), based
on the coordinated 0.13.13 source set. It does not incorporate the separate
0.14.0 workstream or change the initial installer.

## Feature provenance and transaction reporting

- Measure canonical installed payloads and accept production-sized feature
  metadata without blocking the Web UI's event loop.
- Keep corrupt, incomplete, oversized, or unreadable transaction and candidate
  evidence unavailable rather than reporting a successful or empty state.
- Distinguish the current committed installation from older rollback history,
  and validate retained rollback records against the Platform producer format.
- Require verified signed authority, coherent installed-transaction identity,
  and matching current feature bytes before reporting release/source identity.
  A signed release authorizing preserved bytes is not proof of their original
  build source. Missing or invalid authority remains explicitly unavailable.

## OTA v2 maintenance

- Advance the protected Product signing handoff and candidate contract to
  0.13.14, using the published 0.13.13 release as the preceding stable baseline.
- Preserve the existing trusted signing key, v2 schema and feature-plan
  authority, payload/manifest hash binding, complete publication inventory,
  reboot-bound activation, and normal health/confirmation gates.
- Keep the stable discovery alias byte-identical to its canonical versioned
  signed OTA and covered by release metadata and `SHA256SUMS`.

## Source and validation policy

- Product release ID: `radar-puffin-v0.13.14`
- Product version: `0.13.14`
- Release impact: `patch`
- Baseline: coordinated 0.13.13 source set; no unrelated development changes.
- Product, Platform, Linux, and UI use coordinated `release/0.13.14` refs. The
  hosted build must record their exact commits in the candidate manifest and
  `release-source-commits.txt`; UI `VERSION` must equal `0.13.14`.

Source merges and host checks do not establish physical hardware acceptance.
Publication requires reviewed component changes, exact-head checks, the
canonical hosted Product build, independent image/provenance verification,
and complete signed asset validation. Network installation, inactive-slot
readback, reboot, automatic confirmation, rollback testing, and physical
service behavior are separate acceptance evidence; none is implied by this
source note. Provenance implementation and integration checks must be complete
before the release workflow is dispatched.

## Installation and verification

Use only assets attached to the matching published Product release and verify
them against its `SHA256SUMS`. A branch name or source note alone is not an
installable artifact or authorization to install, reboot, or publish. Existing
settings, userdata, and unrelated feature data must be preserved by OTA.

## License and distribution boundary

Distribution remains subject to the public component allowlist and individual
component notices. The community-noncommercial wakeword payload retains its
**CC-BY-NC-SA-4.0** noncommercial, attribution, modification-notice, and
ShareAlike requirements. TTS voice assets retain their separate
**CC-BY-SA-4.0** obligations. Credentials, signing material, device
identifiers, owner-local connectivity firmware, and private build metadata are
excluded from public release metadata.
