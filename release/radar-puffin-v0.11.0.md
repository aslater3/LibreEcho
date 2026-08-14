# LibreEcho radar-puffin v0.11.0 development release

This is a development-channel community-noncommercial release for testing the
signed A/B update path and the stable/development channel selection flow.

## Downloads

Download the signed OTA archive or the initial-install bundle from the GitHub
Release assets. Verify `libreecho-radar-puffin-v0.11.0-SHA256SUMS` before use.
The channel alias for this development release is
`libreecho-radar-puffin-dev.ota.tar`.

## Update behavior

- The device checks the selected channel's signed OTA asset.
- Changing channels clears the previous check result; run a fresh check before
  installing.
- Installation writes only the inactive A/B payload slot and does not reboot
  automatically.
- Confirm the new slot only after the device passes the runtime acceptance
  checks for the target hardware.

## Scope and licensing

This release is `community-noncommercial`. LibreEcho-authored code remains
open source under its stated licenses. The wakeword model retains its CC-BY-NC-SA-4.0 noncommercial and ShareAlike
restrictions. The TTS voices retain their separate CC-BY-SA-4.0 ShareAlike
license without the wakeword model's noncommercial restriction.
Corresponding-source, relink, provenance, notices, and SBOM materials are
retained privately and furnished on request rather than attached as normal
public download assets.

This release has not been flashed or accepted on hardware by this publication
workflow.
