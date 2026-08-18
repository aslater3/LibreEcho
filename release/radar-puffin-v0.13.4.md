# LibreEcho radar-puffin v0.13.4 development release

This controlled `community-noncommercial` prerelease targets the Amazon Echo 2nd
Gen / `radar_puffin` / BISCUIT with the verified ARMv7 Linux 6.1 production
image and self-downloading one-shot installer.

## Downloads

The release includes the signed OTA archive, complete 16 MiB boot image,
initial-install bundle, self-downloading installer, feature payloads and
manifests, OTA public key, release notes, and SHA-256 inventory. Verify
`libreecho-radar-puffin-v0.13.4-SHA256SUMS` before use.

## One-shot installation

Run the installer with only the release tag:

```bash
python3 libreecho-radar-puffin-v0.13.4-installer.py one-shot \
  --release-tag radar-puffin-v0.13.4 \
  --fastboot-serial auto --slots both --execute-hardware
```

The installer downloads and verifies the complete release asset set and the
exact pinned Amonet commit, including all required Git LFS objects. It then:

1. runs the physical-short Amonet BROM handoff and reports its live progress;
2. waits for BISCUIT fastboot;
3. flashes the verified LibreEcho boot image to logical `boot_a` and `boot_b`;
4. erases only `expdb`;
5. waits for ADB and verifies the mapped `boot_a_x`/`boot_b_x` payload hashes;
6. **stages all five feature payloads (airplay2, assistant, stt, tts, wakeword)
   into userdata** through the root runner, verifying every payload hash before
   and after installation and replacing each feature atomically;
7. forwards host TCP 18080 to device TCP 8080 and opens:

```text
http://127.0.0.1:18080/setup.html
```

Feature packages are SquashFS images and cannot be installed through fastboot;
the one-shot now installs them through the rooted device after boot.

A virgin stock device still requires the physical Amonet BROM short. Complete
Wi-Fi, hostname, account, privacy, and other first-boot settings in the browser
wizard after installation.

## Scope and licensing

This is a `community-noncommercial` release. LibreEcho-authored code remains
open source under its stated licenses. The wakeword model retains its
CC-BY-NC-SA-4.0 noncommercial and ShareAlike restrictions. TTS voice assets
retain their separate CC-BY-SA-4.0 terms. Review payload manifests and notices
before redistribution or modification.

This is a controlled hardware-test prerelease. Host verification and a signed
OTA do not by themselves claim general hardware acceptance.
