# LibreEcho radar-puffin v0.13.17

LibreEcho 0.13.17 is a focused installer and userdata sparse-image compatibility patch for the Amazon Echo 2nd Gen (`radar_puffin`). It includes the checksum-inventory correction from 0.13.16 and the newer ext4-allocation-based userdata sparse-image generator.

## Installer checksum inventory compatibility

- Validate the signed OTA v2 feature-asset inventory before accepting additional checksum-covered release files.
- Permit the feature plan, feature inventory, and only the replacement asset names declared by that validated inventory.
- Continue rejecting undeclared checksum-covered files and malformed inventories.

## Userdata sparse-image generation

- Generate Android sparse RAW/DONT_CARE chunks from validated ext4 allocation metadata instead of host filesystem holes.
- Copy every allocated ext4 block, including zeroed metadata, journals, inode tables, and directory padding; skip only ext4-free blocks.
- Validate complete per-group free-block lists and preserve the bounded physical-write guard before flashing userdata.
- Keep the exact reviewed userdata geometry allowlist and the matching Platform-side first-boot, staging, updater, and bootctl checks.

This carries the Product fix from PR #172 and the coordinated Platform fix from PR #161 onto the `0.13.17` release line. It addresses the sparse-image failure tracked in issue #163 while retaining the issue #162 checksum fix.

## Source and validation policy

- Product release ID: `radar-puffin-v0.13.17`
- Product version: `0.13.17`
- Release impact: `patch`
- Baseline: coordinated `release/0.13.16` source set.
- Product, Platform, Linux, and UI use coordinated `release/0.13.17` refs. The hosted build must record their exact commits in the candidate manifest and `release-source-commits.txt`; UI `VERSION` must equal `0.13.17`.

Source and host checks do not establish physical hardware acceptance. Publication requires the canonical hosted Product build, independent image/provenance verification, complete signed asset validation, and live installation testing.

## Installation and verification

Use only assets attached to the matching published Product release and verify them against its `SHA256SUMS`. Do not mix assets from `0.13.16`, development builds, or nightly releases.

## License and distribution boundary

Distribution remains subject to the public component allowlist and individual component notices. Credentials, signing material, device identifiers, owner-local connectivity firmware, and private build metadata are excluded from public release metadata.
