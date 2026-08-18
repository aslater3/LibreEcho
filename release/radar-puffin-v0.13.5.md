# LibreEcho radar-puffin v0.13.5 development release

This controlled `community-noncommercial` prerelease targets the Amazon Echo 2nd
Gen / `radar_puffin` / BISCUIT with the verified ARMv7 Linux 6.1 production
image and self-downloading installer.

## Fixes in this release

- Restores the green startup LED chase until the production service graph is
  ready, with a bounded frame cadence and an atomic readiness hand-off.
- Publishes Bluetooth readiness only after controller setup is complete.
- Preserves SBC decoder synthesis state across A2DP frames and resets it only
  at stream start, preventing frame-boundary audio artifacts.
- Retains the existing Bluetooth profile, audio-period pacing, and rollback
  behavior.

## Downloads

The release includes the signed OTA archive, complete 16 MiB boot image,
initial-install bundle, self-downloading installer, feature payloads and
manifests, OTA public key, release notes, and SHA-256 inventory. Verify the
release `SHA256SUMS` file before use.

## Scope and validation

This is a controlled hardware-test prerelease. Host image verification and a
signed OTA do not by themselves claim general hardware acceptance. Physical
Bluetooth playback was supervised successfully from a MacBook during reversible
validation; iPhone-specific playback remains an open validation item.

This is a `community-noncommercial` release. The wakeword model retains its
CC-BY-NC-SA-4.0 restrictions, and TTS voice assets retain their separate
license terms. Review payload manifests and notices before redistribution or
modification.
