# LibreEcho radar-puffin v0.13.2 development release

This controlled `community-noncommercial` prerelease targets the Amazon Echo 2nd
Gen / `radar_puffin` / BISCUIT with the verified ARMv7 Linux 6.1 production
image and self-downloading one-shot installer.

## Downloads

The release includes the signed OTA archive, complete 16 MiB boot image,
initial-install bundle, self-downloading installer, feature payloads and
manifests, OTA public key, release notes, and SHA-256 inventory. Verify
`libreecho-radar-puffin-v0.13.2-SHA256SUMS` before use.

## One-shot installation

Run the installer with only the release tag:

```bash
python3 libreecho-radar-puffin-v0.13.2-installer.py one-shot \
  --release-tag radar-puffin-v0.13.2 \
  --fastboot-serial auto --slots both --execute-hardware
```

The installer downloads and verifies the complete release asset set and the
exact pinned Amonet commit, including all required Git LFS objects such as
`lk.bin`, `tz.img`, the preloader, wrapper payloads, and diagnostic boot image.
It then runs the physical-short BROM handoff, flashes logical `boot_a` and
`boot_b`, verifies the mapped Amonet payloads, forwards the Web UI over ADB,
and opens:

```text
http://127.0.0.1:18080/setup.html
```

A virgin stock device still requires the physical Amonet BROM short. Complete
Wi-Fi, hostname, account, privacy, and other first-boot settings in the browser
wizard. Do not reuse credentials in release notes or command history.

## Scope and licensing

This is a `community-noncommercial` release. LibreEcho-authored code remains
open source under its stated licenses. The wakeword model retains its
CC-BY-NC-SA-4.0 noncommercial and ShareAlike restrictions. TTS voice assets
retain their separate CC-BY-SA-4.0 terms. Review payload manifests and notices
before redistribution or modification.

This is a controlled hardware-test prerelease. Host verification and a signed
OTA do not by themselves claim general hardware acceptance.
