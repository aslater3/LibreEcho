# LibreEcho radar-puffin v0.13.15

LibreEcho 0.13.15 is a focused compatibility hotfix for the Amazon Echo 2nd
Gen (`radar_puffin`, ARMv7, Linux 6.1), based on the coordinated 0.13.14 source
set. It contains two functional fixes only and does not incorporate the separate
0.14.0 workstream or change feature payloads.

## MT8163 owner-local connectivity firmware compatibility

- Recognize the second independently validated four-file MT8163 stock firmware
  revision reported on full-size Echo Gen 2 hardware as a complete hash-pinned
  revision.
- Match approved revisions atomically; files or hashes from different revisions
  are never combined to manufacture an accepted set.
- Preserve the explicit one-boot structural force gate for a future owner-local
  revision that LibreEcho has never seen. After a successful forced import,
  persist only the exact SHA-256/size manifest locally with mode `0600`; vendor
  firmware bytes remain on the owner's stock partition and are not persisted or
  redistributed by LibreEcho.
- Require the exact enrolled manifest on later boots and fail closed if the
  enrolled set changes.
- Report a real no-approved-revision result when safe regular files are present,
  rather than allowing an unrelated symlink candidate to mask that result.

## Stable factory Wi-Fi MAC

- Restore the missing `CONFIG_IDME` Kconfig contract for the forward-ported
  MT8163 CONSYS/WLAN stack and enable it in `mt8163_arm32_defconfig`.
- Compile the retained factory IDME path so Radar/Puffin can use
  `/idme/mac_addr` instead of the driver's time-derived `00:08:22:*` fallback
  when the calibration MAC path is unavailable.
- Hardware acceptance for this fix requires confirming the live `wlan0` address
  equals the factory IDME value across warm and cold reboots and that DHCP
  identity remains stable.

## OTA v2 maintenance

- Advance the protected Product signing handoff and candidate contract to
  0.13.15, using the published 0.13.14 release as the preceding stable baseline.
- Preserve the existing trusted signing key, v2 schema and feature-plan
  authority, payload/manifest hash binding, complete publication inventory,
  reboot-bound activation, and normal health/confirmation gates.
- No feature payload generation is intentionally changed by this patch release.

## Source and validation policy

- Product release ID: `radar-puffin-v0.13.15`
- Product version: `0.13.15`
- Release impact: `patch`
- Baseline: coordinated 0.13.14 source set; no unrelated development changes.
- Product, Platform, Linux, and UI use coordinated `release/0.13.15` refs. The
  hosted build must record their exact commits in the candidate manifest and
  `release-source-commits.txt`; UI `VERSION` must equal `0.13.15`.

Source merges and host checks do not establish physical hardware acceptance.
Publication requires reviewed component changes, exact-head checks, the
canonical hosted Product build, independent image/provenance verification, and
complete signed asset validation. For this hotfix, hardware validation must also
cover both supported hash-pinned stock firmware revisions and stable IDME-backed
Wi-Fi identity across reboots before publication.

## Installation and verification

Use only assets attached to the matching published Product release and verify
them against its `SHA256SUMS`. A branch name or source note alone is not an
installable artifact or authorization to install, reboot, or publish. Existing
settings, userdata, locally enrolled vendor-asset hashes, and unrelated feature
data must be preserved by OTA.

## License and distribution boundary

Distribution remains subject to the public component allowlist and individual
component notices. Credentials, signing material, device identifiers,
owner-local connectivity firmware, and private build metadata are excluded from
public release metadata. The new MT8163 compatibility manifest contains only
file identities and sizes; no Amazon firmware bytes are added to the release.
