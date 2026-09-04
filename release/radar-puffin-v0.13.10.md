# LibreEcho radar-puffin v0.13.10

LibreEcho 0.13.10 is the stable Product release for Amazon Echo 2nd Gen
(`radar_puffin`, ARMv7, Linux 6.1). It rolls the post-0.13.9 installation,
first-boot, service-readiness, networking, LED handoff, and mDNS corrections
into one coordinated source set.

## Highlights

- The one-shot installer has clearer BROM guidance, readable persistent logs,
  guarded userdata recovery, and verified feature-payload staging.
- First-boot setup keeps failures retryable, applies supported Alexa wake
  settings, validates the selected service graph, and confirms startup
  readiness before completing.
- Setup completion links use the device's canonical port `8080`.
- Hostname changes refresh the managed Avahi/AirPlay registration stack, so
  `libreecho.local` remains resolvable after setup and later hostname changes.
- Wi-Fi setup and status reporting include stronger security, scan, and
  connectivity handling.
- Feature services start only after their verified payloads are available.
- LED startup ownership is handed off cleanly without the transient white
  frame corrected during 0.13.10 validation.

## Installation

New installations must follow the Product [Echo 2nd Gen one-shot installation
guide](../docs/install/README.md). Its checksum-gated command downloads the
exact stable bootstrap, verifies the installer before execution, performs the
guarded BROM-to-fastboot handoff, verifies both boot slots, and stages all five
feature payloads.

Existing LibreEcho installations can use the signed A/B OTA bundle. Preserve a
confirmed rollback slot and verify the release checksum inventory before use.

## Validation

The exact coordinated 0.13.10 candidate was built and signed by the hosted
Product pipeline, independently verified, installed by signed OTA on authorized
Echo 2nd Gen hardware, and checked for exact boot-image identity, setup and
service readiness, rollback availability, network health, AirPlay operation,
and live mDNS hostname transitions. `http://libreecho.local:8080/` remained
reachable after a bounded soak.

The stable publication pipeline binds the final Product, Platform, Linux 6.1,
and UI commit identities into the public build metadata and generated
cross-repository change ledger.

## Downloads and verification

The release contains the signed OTA, boot image, initial-install bundle,
checksum-gated one-shot bootstrap, installer, OTA public key, five feature
payloads and manifests, release notes, build metadata, and `SHA256SUMS`.
Verify the published checksum inventory before installation and do not mix
assets from different release tags.

## License and distribution

This release is `community-noncommercial`. The wakeword model remains licensed
under **CC-BY-NC-SA-4.0**; attribution, noncommercial use, indication of
modifications, and ShareAlike requirements apply. TTS voices and other bundled
components retain their individual notices and licence obligations. Review the
release notices and source-closure records before redistribution.