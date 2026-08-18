# LibreEcho radar-puffin v0.13.6 development release

This controlled `community-noncommercial` prerelease targets the Amazon Echo 2nd
Gen / `radar_puffin` / BISCUIT with the verified ARMv7 Linux 6.1 production
image and self-downloading one-shot installer.

## Changes in this release

- Same freshly built firmware image as v0.13.5 (green startup LED chase,
  Bluetooth readiness gating, SBC decoder state preservation).
- Installer fix: the one-shot installer now accepts the optional
  `libreecho-radar-puffin-dev.ota.tar` dev-OTA alias in the release checksum
  inventory, which previously caused a `checksum inventory mismatch` and
  stopped the download before Amonet could start.

## Downloads

The release includes the signed OTA archive, complete 16 MiB boot image,
initial-install bundle, self-downloading installer, feature payloads and
manifests, OTA public key, release notes, and SHA-256 inventory. Verify the
release `SHA256SUMS` file before use.

## One-shot installation

```bash
python3 libreecho-radar-puffin-v0.13.6-installer.py one-shot \
  --release-tag radar-puffin-v0.13.6 \
  --fastboot-serial auto --slots both --execute-hardware
```

The installer downloads and verifies the complete release asset set and the
pinned Amonet commit, runs the physical-short BROM handoff, flashes logical
`boot_a` and `boot_b`, verifies the mapped payloads, stages all five feature
payloads into userdata, forwards the Web UI, and opens:

```text
http://127.0.0.1:18080/setup.html
```

## Scope and licensing

This is a `community-noncommercial` release. The wakeword model retains its
CC-BY-NC-SA-4.0 restrictions, and TTS voice assets retain their separate
license terms. Review payload manifests and notices before redistribution or
modification.

This is a controlled hardware-test prerelease. Host verification and a signed
OTA do not by themselves claim general hardware acceptance.
