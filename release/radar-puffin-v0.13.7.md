# LibreEcho radar-puffin v0.13.7 development release

This is a controlled **development-channel, dev-only prerelease** for the
Amazon Echo 2nd Gen / `radar_puffin` / BISCUIT target.

## Release scope

- OTA update channel: `dev`
- Release classification: GitHub prerelease
- Intended use: development and controlled testing only
- This release is not a stable or production-support commitment.

The release assets contain the signed OTA archive, boot image, initial-install
bundle, feature payloads and manifests, OTA public key, installer, release
notes, and SHA-256 inventory. Verify the published `SHA256SUMS` file before
use.

The build uses public-release packaging. The private build Wi-Fi profile is
used only as a local build input and is replaced by the credential-free public
Wi-Fi configuration before the public boot image is assembled. SSID and PSK
are not included in the release assets.

A signed OTA and host verification do not by themselves establish hardware
acceptance. Hardware deployment and runtime validation are separate gates and
are not part of this release publication.

The wakeword payload remains subject to its `CC-BY-NC-SA-4.0` license and the
other feature payloads retain their respective license terms.
