# LibreEcho radar-puffin v0.13.0 development release

This controlled `community-noncommercial` prerelease targets the Amazon Echo 2nd
Gen / `radar_puffin` / BISCUIT with the verified ARMv7 Linux 6.1 production
image from the 0.13.0 candidate.

## Downloads

The release includes the signed OTA archive, the complete 16 MiB boot image,
the initial-install bundle and installer, feature payloads and manifests, the
OTA public key, release notes, and a SHA-256 inventory. Verify
`libreecho-radar-puffin-v0.13.0-SHA256SUMS` before use.

## One-shot installation

The one-shot installer runs the pinned Amonet BROM handoff, flashes LibreEcho
to logical `boot_a` and `boot_b`, verifies the mapped Amonet payloads, forwards
the Web UI over ADB, and opens the first-boot setup page.

The one-shot installer downloads the release assets and the exact pinned Amonet
commit archive automatically; no local Amonet checkout or manually prepared
release directory is required. A virgin stock device still requires the
physical Amonet BROM short.

does not publish or modify Amonet/vendor boot-chain material, connectivity
firmware, credentials, or signing keys.

After the handoff, complete the user configuration at:

```text
http://127.0.0.1:18080/setup.html
```

The browser wizard configures Wi-Fi, hostname, account, privacy, and other
first-boot settings. Do not reuse credentials in release notes or command
history.

## Scope and licensing

This is a `community-noncommercial` release. LibreEcho-authored code remains
open source under its stated licenses. The wakeword model retains its
CC-BY-NC-SA-4.0 noncommercial and ShareAlike restrictions. TTS voice assets
retain their separate CC-BY-SA-4.0 terms. Review payload manifests and notices
before redistribution or modification.

This is a controlled hardware-test prerelease. Host verification and a signed
OTA do not by themselves claim general hardware acceptance.
