# LibreEcho radar-puffin v0.13.9 development release

This is a controlled **development-channel test release** for the Amazon Echo
2nd Gen / `radar_puffin` target. It is intended for the maintainer’s test
hardware and is not a stable or production-support commitment.

## Release identity

- Product release: `radar-puffin-v0.13.9`
- OTA channel: `dev`
- Release classification: GitHub prerelease
- Coordinated components: Product, Platform, Linux 6.1, and UI source heads
- Intended use: maintainer-controlled test flashing and validation

The image candidate must record the exact Product, Platform, Linux, and UI
commits used to create it. Development assets remain separate from stable
publication and must not be marked `latest`.

## Validation boundary

The 0.13.9 test candidate must pass the deterministic ARM32 image build, DTB
hardware-contract verifier, source/provenance closure, signed A/B OTA checks,
and the relevant host-side component suites before it is flashed. A successful
host or image build is not hardware acceptance.

Physical flashing, boot, rollback, network, audio, voice, privacy, and runtime
acceptance are separate gates. This note does not claim that those checks have
been completed. No device is touched by the release-preparation workflow.

The community-noncommercial wakeword payload and any other bundled third-party
assets retain their individual licence and redistribution terms.
