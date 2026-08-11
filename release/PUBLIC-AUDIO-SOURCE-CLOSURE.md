# Public audio source and relink closure

This record covers the host-verified `redistributable` LibreEcho production
profile. It is release evidence, not a publication, flash, boot, playback,
capture, FPGA-runtime, or device-runtime claim.

## Candidate identity

This record is bound to the sanitized source and artifact identities below.
- Linux source: `19efd9685e22f96cf1cb70551eae4c0075692e5c`
- Platform source: `304806a69bc9df87b12e95e98252a011b26934d8`
- UI source: `50b9dedb9eedb21b0ea45b805421138a061db253`
- Build source: `7a512a329293cc9325fb620a3f9c088a2ffdfe1a`
- Feature policy: `redistributable`
- Boot SHA-256: `6e5fffd4ca39bfcc3413a78cad408daa5613dade6a1ac0b2dcee79257ad2126c`
- Source-offer index SHA-256: `17c04f059fd3ec017719e7991d161b2785761ceb1636c800197affe3a3aa2a06`
- Signed OTA SHA-256: `972a1715b569778c341fffd44bc9023cf1fb78d12863111e29c35bd3afb4349c`

## Corresponding-source offers

The independently checked index contains exactly the five redistributed
components below. Archive hashes, sidecar manifests, embedded manifests, and
every member size/hash were re-read independently: 3,410 members in total.

| Component | Source-offer SHA-256 | Members |
|---|---|---:|
| Core runtime closure | `73140d22a1ddbf501b4da362c12796baa263a786d90e86e6ab7705871354463b` | 106 |
| AirPlay payload | `062eba536d790179b63e39540dace6497f49d3ae43e4c7e336d2b1e9917384c7` | 438 |
| STT payload | `7c1feabcb785347375a5595b067e09cd618094138c8bd46daa348dcdf8a229de` | 1,158 |
| TTS payload | `9ee10035fb196d01570907818c4b65458bc87529aff9355c247e010298c9e505` | 1,158 |
| Assistant payload | `a68715579adfa75319b0590eb71f5873bb4a4ac12ee59652110c40dc9e82ec72` | 550 |

The core offer includes the exact source archives and glibc/GCC runtime relink
closure. Feature offers include pinned upstream/project source, notices, and
relink objects used by the candidate.

## Public feature boundary

The image manifest enables AirPlay, STT, TTS, and Assistant. Its wakeword record
is disabled. The userdata tree contains exactly `airplay2`, `stt`, `tts`, and
`assistant`; no wakeword payload or manifest exists. The source-offer index has
no wakeword entry. The signed OTA manifest carries
`feature_policy=redistributable` and its signature and boot payload hash were
verified independently.

The Alexa-compatible wakeword model remains `CC-BY-NC-SA-4.0`, is classified as
a restricted noncommercial payload, and is not redistributed in this profile.

## Verification boundary

`status.sh` and the independent archive/OTA verifier passed. The immutable run
is `PREPARED_NOT_FLASHED`; canonical `out/CURRENT` was not changed. Publication,
flashing, reboot, hardware acceptance, audio playback/capture, and slot
confirmation were not performed.
