# LibreEcho radar-puffin v0.13.16

LibreEcho 0.13.16 is a focused installer compatibility patch for the Amazon Echo 2nd Gen (`radar_puffin`). It republishes the stable one-shot installer from the corrected Product source so OTA v2 releases with signed feature inventories can be validated and device detection can proceed.

## Installer checksum inventory compatibility

- Validate the signed OTA v2 feature-asset inventory before accepting additional checksum-covered release files.
- Permit the feature plan, feature inventory, and only the replacement asset names declared by that validated inventory.
- Continue rejecting undeclared checksum-covered files and malformed inventories.
- Include the corrected installer and regenerated SHA-256 sidecar in the stable release.

This addresses issue #162. The existing `radar-puffin-v0.13.15` asset remains immutable; use this release's matching `SHA256SUMS` file and installer.

## Source and validation policy

- Product release ID: `radar-puffin-v0.13.16`
- Product version: `0.13.16`
- Release impact: `patch`
- Baseline: coordinated `release/0.13.15` source set, with the Product installer fix from PR #166 and the UI version marker advanced to `0.13.16`.
- Product, Platform, Linux, and UI use coordinated `release/0.13.16` refs. The hosted build must record their exact commits in the candidate manifest and `release-source-commits.txt`; UI `VERSION` must equal `0.13.16`.

Source and host checks do not establish physical hardware acceptance. Publication requires the canonical hosted Product build, independent image/provenance verification, and complete signed asset validation.

## Installation and verification

Use only assets attached to the matching published Product release and verify them against its `SHA256SUMS`. Do not mix assets from `0.13.15`, development builds, or nightly releases.

## License and distribution boundary

Distribution remains subject to the public component allowlist and individual component notices. Credentials, signing material, device identifiers, owner-local connectivity firmware, and private build metadata are excluded from public release metadata.
