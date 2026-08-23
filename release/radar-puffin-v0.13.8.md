# LibreEcho radar-puffin v0.13.8 Developer / Early Access Beta

This is the planned **development-channel, controlled Developer / Early Access
Beta** release identity for the Amazon Echo 2nd Gen / `radar_puffin` target.
It is not a stable or production-support commitment.

## Release identity

- Product release: `radar-puffin-v0.13.8`
- OTA channel: `dev`
- Release classification: GitHub prerelease
- Coordinated components: Product, Platform, and UI `0.13.8` release heads
- Intended audience: supported Echo Gen 2 owners with a verified recovery path

The product release ID is the immutable authority. Development and stable OTA
aliases, when published, must resolve to an exact candidate carrying this same
identity; an alias must not replace the immutable release identity.

## Integrated scope

The 0.13.8 release heads coordinate the current release-branch work across the
component repositories, including:

- bounded voice capture, wake-word, audio-quality, assistant, weather, and
  speech-pipeline improvements;
- persisted LED state, night-mode controls, startup/readiness handling, and
  bounded diagnostics export;
- Bluetooth pairing presentation, input capability reporting, Wi-Fi security
  contract handling, and retention-state validation;
- OTA rollback/data-contract safety, preserved-payload identity checks, AirPlay
  volume scoping, and UI startup readiness validation; and
- corresponding host-side tests and source/API contract updates.

The exact image feature set, payload identities, artifact names, checksums, and
source manifest remain candidate-build outputs and must be generated from one
clean, immutable source set before publication.

## Validation boundary and remaining gates

Host/source checks are not hardware acceptance. This release note intentionally
does not claim that the 0.13.8 candidate has been built, published, installed,
OTA-updated, rollback-tested, or accepted on physical hardware.

Before publishing the prerelease, the release gate still requires the exact
candidate to pass the deterministic image build, source/provenance closure,
initial-install and signed A/B OTA checks, runtime identity checks, recovery and
rollback exercises, network/media/voice/privacy acceptance, and the required
external tester evidence. Unresolved release-gate issues remain tracked in the
Product 0.13.8 cross-repository release issue.

When assets are published, users must verify the release-provided checksum and
public-key inventory and follow the supported installation/recovery guidance.
Do not use an older `latest` or development artifact as evidence for this
release.

The community-noncommercial wakeword payload and any other bundled third-party
assets retain their individual licence and redistribution terms. The
`CC-BY-NC-SA-4.0` wakeword restriction remains applicable where that payload is
included.
