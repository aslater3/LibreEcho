# LibreEcho radar-puffin v0.1.0 community prerelease

This is the first public **prerelease** of LibreEcho for the Amazon Echo 2nd Gen
(`radar_puffin`, ARMv7, Linux 6.1). It is a development-channel,
`community-noncommercial` artifact set for review and controlled installation.

## Release verification

The boot image, signed OTA, payloads, and corresponding-source identities have
been independently verified and are listed in the release provenance and
`radar-puffin-v0.1.0-SHA256SUMS` assets.

This prerelease was prepared and verified on the host. No device was flashed,
rebooted, or runtime-accepted as part of this publication. It has **not been
tested on a physical device**, so hardware compatibility and runtime behavior
are not claimed here.

## Downloads

- **OTA:** use the signed `.ota.tar` with the device OTA flow.
- **Initial install:** download the initial-install bundle for a manual first
  installation. It contains the boot image, feature payloads, manifests, and
  OTA public key.
- **Checksums:** use `SHA256SUMS` to verify both downloaded archives before
  use.

The corresponding-source offers, SBOM, notices, and detailed provenance are
kept privately and are available by request. They are not part of the normal
user download set.

The release excludes credentials, signing keys, device identifiers,
calibration data, Amonet/vendor boot-chain material, and MT8163 connectivity
firmware. Connectivity firmware remains an **owner-device-local** extraction
input and is not redistributed.

## License boundary

LibreEcho-authored product code is MIT-licensed, but the complete artifact set
contains separately licensed components. In particular, the wakeword model is
licensed under **CC-BY-NC-SA-4.0**: use is **noncommercial**, attribution is
required, and adapted material remains subject to **ShareAlike**. TTS voice
assets include CC-BY-SA-4.0 material. Review `THIRD_PARTY_NOTICES.md`,
`COMMUNITY-NONCOMMERCIAL-SOURCE-CLOSURE.md`, the SPDX SBOM, and embedded payload
notices before redistribution or modification.

Do not describe this community prerelease as permitting unrestricted commercial
reuse.

## Source identities

- Final Product commit: see the sanitized release provenance asset generated
  from the catalog-attesting release candidate.
- Linux 6.1 kernel: `a6c4b01faae9b937f9067d4d14ee0917f662577c`
- Platform/tooling: `583cfbcc400767eb01187bf123abe6ad0dca824c`
- Web UI/services: `021001d035c6044b7f78841434ac3e58c48c5708`

## Verification and installation caution

Verify `radar-puffin-v0.1.0-SHA256SUMS` before use. The signed OTA contains
exactly `boot.img`, `manifest`, and `manifest.sig`; its Ed25519 signature and
host image contract were independently verified before publication.

Do not flash a raw or renamed artifact outside the reviewed LibreEcho deployment
flow. Preserve a confirmed rollback slot and owner data, and treat successful
host verification as distinct from device runtime acceptance.
