# LibreEcho radar-puffin v0.13.13

LibreEcho 0.13.13 is a focused OTA v2 maintenance release for the Amazon Echo
2nd Gen (`radar_puffin`, ARMv7, Linux 6.1), based on the coordinated 0.13.12
source set. It preserves the network OTA path and does not add or alter the
initial installer.

## OTA v2 maintenance

- Advance the protected Product signing handoff and candidate contract to
  0.13.13.
- Pin the v2 input contract to the published 0.13.12 feature catalog as the
  preceding stable baseline, while retaining checksum verification and the
  device signature trust boundary.
- Move the explicit development-device migration guard to
  `release/0.13.13`; other channels, events, and branches remain rejected.
- Keep signer provenance bound to the v2 candidate, trusted OTA public-key
  digest, measured source/tool/dependency identities, and complete feature
  asset inventory.

## Source and validation policy

- Product release ID: `radar-puffin-v0.13.13`
- Product version: `0.13.13`
- Release impact: `patch`
- Baseline: coordinated 0.13.12 source set; no unrelated development changes.
- Product, Platform, Linux, and UI use coordinated `release/0.13.13` refs. The
  hosted build must record their exact commits in the candidate manifest and
  `release-source-commits.txt`; UI `VERSION` must equal `0.13.13`.

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
