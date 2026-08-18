# LibreEcho radar-puffin v0.13.3 development release

This controlled `community-noncommercial` prerelease targets the Amazon Echo 2nd
Gen / `radar_puffin` / BISCUIT with the verified ARMv7 Linux 6.1 production
image and self-downloading one-shot installer.

## Downloads

The release includes the signed OTA archive, complete 16 MiB boot image,
initial-install bundle, self-downloading installer, feature payloads and
manifests, OTA public key, release notes, and SHA-256 inventory. Verify
`libreecho-radar-puffin-v0.13.3-SHA256SUMS` before use.

## One-shot installation

Run the installer with only the release tag:

```bash
python3 libreecho-radar-puffin-v0.13.3-installer.py one-shot \
  --release-tag radar-puffin-v0.13.3 \
  --fastboot-serial auto --slots both --execute-hardware
```

The installer downloads and verifies the complete release asset set and the
exact pinned Amonet commit, including all required Git LFS objects. During the
BROM handoff it monitors Amonet's live log and reports state transitions such as
BROM detection, payload activation, GPT/RPMB checks, boot-chain writes, and
fastboot preparation. It no longer reports a generic BROM wait while Amonet is
already operating the device.

A virgin stock device still requires the physical Amonet BROM short. Complete
Wi-Fi, hostname, account, privacy, and other first-boot settings in the browser
wizard after the installer opens:

```text
http://127.0.0.1:18080/setup.html
```

## Scope and licensing

This is a `community-noncommercial` release. LibreEcho-authored code remains
open source under its stated licenses. The wakeword model retains its
CC-BY-NC-SA-4.0 noncommercial and ShareAlike restrictions. TTS voice assets
retain their separate CC-BY-SA-4.0 terms. Review payload manifests and notices
before redistribution or modification.

This is a controlled hardware-test prerelease. Host verification and a signed
OTA do not by themselves claim general hardware acceptance.
