# LibreEcho radar-puffin v0.13.12

LibreEcho 0.13.12 is a focused OTA v2 maintenance release for the Amazon Echo
2nd Gen (`radar_puffin`, ARMv7, Linux 6.1), based on the coordinated 0.13.11
source set. It keeps the v2 update path and does not add or alter the initial
installer.

## OTA v2 maintenance

- Advance the protected Product signing handoff to the 0.13.12 release.
- Keep the signer bound to v2 candidates, the trusted OTA public-key digest,
  measured source/tool/dependency identities, and the complete feature asset
  inventory.
- Authorize v2 device-migration inputs only for `release/0.13.12`, using the
  published 0.13.11 feature catalog as the pinned prior-release baseline.

## Source and validation policy

- Product release ID: `radar-puffin-v0.13.12`
- Product version: `0.13.12`
- Release impact: `patch`
- Baseline: coordinated 0.13.11 source set; no unrelated development changes.
- Product, Platform, Linux, and UI use coordinated `release/0.13.12` refs. The
  hosted build must record their exact commits in the candidate manifest and
  `release-source-commits.txt`; UI `VERSION` must equal `0.13.12`.

Merging source is not publication or proof of hardware behavior. Publication
requires component checks, the canonical hosted Product build, independent
image/provenance verification, and separately authorized OTA signature,
inactive-slot readback, boot, rollback, and service-readiness acceptance.

## Installation and verification

Use only assets attached to the matching published Product release and verify
them against its `SHA256SUMS`. A branch name or source note alone is not an
installable artifact or authorization to install, reboot, or publish. This
release does not change the initial-install path.

## License and distribution boundary

Distribution remains subject to the public component allowlist and individual
component notices. The community-noncommercial wakeword payload retains its
**CC-BY-NC-SA-4.0** noncommercial, attribution, modification-notice, and
ShareAlike requirements. TTS voice assets retain their separate
**CC-BY-SA-4.0** obligations. Credentials, signing material, device
identifiers, owner-local connectivity firmware, and private build metadata are
excluded from public release metadata.
