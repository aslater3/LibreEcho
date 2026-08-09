# LibreEcho third-party notices and distribution boundaries

LibreEcho is a mixed-license collective work. Source code, model weights,
firmware data, and third-party runtimes remain under their respective licenses.
Nothing in this file relicenses a third-party work.

The machine-readable component catalog is `release/components.json`. Exact
source commits, payload hashes, and artifact hashes are recorded in the release
provenance and SPDX 2.3 SBOM. Each separate feature payload also embeds its own
complete notices and component inventory.

## Core boot image and OTA

- **Linux 6.1 and MT8163 product drivers** — GPL-2.0-only. Exact corresponding
  source is released from
  <https://github.com/aslater3/LibreEcho-Linux-6.1>.
- **LibreEcho Platform/initramfs tooling** — GPL-2.0-only. Exact corresponding
  source is released from <https://github.com/aslater3/LibreEcho-Platform>.
- **LibreEcho UI/services** — MIT. Exact source is released from
  <https://github.com/aslater3/LibreEcho-UI>.
- **AOSP `adbd`** — Apache-2.0, source-built at commit
  `4f247d753a8865cd16292ff0b720b72c28049786`; NOTICE is embedded.
- **BusyBox 1.37.0** — GPL-2.0-only. The release rebuilds it from the pinned
  upstream archive and checked-in public configuration; the source archive,
  configuration, build metadata, and exact shipped-binary hash accompany the
  candidate.
- **musl 1.2.5** — MIT, rebuilt from the pinned upstream archive.
- **`wpa_supplicant` 2.10** — BSD-3-Clause; the binary's complete `-L` notice is
  embedded.
- **wireless-tools, wireless-regdb, libsodium, and TinyALSA** are source-locked and
  independently checked or rebuilt for the exact core-image/OTA outputs. Their
  GPL/LGPL, ISC, and BSD-3-Clause terms remain applicable.
- **glibc and GCC runtime code** retain their LGPL and GCC Runtime Library
  Exception terms. Their exact corresponding
  source archives, static-link relinkable objects, and build records remain an
  open aggregate release blocker; see `release/CORE-RUNTIME-SOURCE-OFFER.md`.

### MT8163 audio FPGA bridge — included, documented-good-faith exception

`i2s_to_spi_v34.bin` is required by the known working microphone/audio FPGA
path and is included in the audio-capable candidate through the kernel's
`CONFIG_EXTRA_FIRMWARE` path. Its provenance and deliberate release decision
are documented in `release/FPGA-PROVENANCE.md` and Platform `firmware/WHENCE`.

No firmware-specific licence was found. The candidate therefore retains
`license=NOASSERTION`; the release gate accepts this component only because the
exact origin, community precedent, known-good size/hash, and explicit
non-licence finding are recorded. This is not a claim that postmarketOS or the
Linux package metadata grants a firmware-specific licence.

Known-good identity:

`77a558bacdaaf9e343f02f2d74f27a5f2bb2dc8b6d66cc2499b60ed14ef62fe6`

Any future replacement must match the recorded 30,964-byte size and SHA-256 or
be treated as a new release decision.

## Separate feature payloads

The following SquashFS files are independent release assets. Each must be
cleared for the selected release scope, preserve its own license conditions,
and pass the exact source-offer gate before release. The wakeword asset is
allowed only in the explicitly labelled `community-noncommercial` scope.

### AirPlay 2

The AirPlay payload contains Shairport Sync 5.1, NQPTP 1.2.8, a minimal FFmpeg
7.1.1 build, TinyALSA, Avahi, D-Bus, OpenSSL and their dynamic dependency
closure. The payload embeds:

- source license files for Shairport Sync, NQPTP, FFmpeg, and TinyALSA;
- the exact Debian copyright record for every copied runtime package;
- ALSA data-package copyright records; and
- a component/source-archive SHA-256 inventory.

Copyleft source is supplied from the pinned upstream archives and exact Debian
source packages recorded by the payload inventory.

### Speech to text

The STT payload contains sherpa-onnx, ONNX Runtime, SpeexDSP, and the pinned
Apache-2.0 streaming Zipformer model. Its model notice, runtime notices, source
commits, and payload hash accompany the asset.

### Text to speech

The TTS payload contains sherpa-onnx, ONNX Runtime, eSpeak NG data, and two
Piper voices:

- `en_GB-northern_english_male-medium` — CC-BY-SA-4.0, OpenSLR SLR83;
- `en_GB-southern_english_female-low` — CC-BY-SA-4.0, OpenSLR SLR83.

Both ONNX graphs are unchanged from the pinned Piper revision
`ea046e8458f6acd997706d6e6066a022b42f6fb1`; LibreEcho adds only descriptive
metadata. Upstream and resulting hashes, model cards, attribution, and the full
CC-BY-SA-4.0 legal text are embedded. The prior `alan` model is not distributed
because its cited per-voice source record says “All Rights Reserved.”

### Wake word

The wakeword models come from openWakeWord v0.5.1 and are licensed
CC-BY-NC-SA-4.0. They are distributed as a distinct asset only for uses allowed
by that license. Recipients must preserve attribution, use the models
noncommercially, indicate modifications, and ShareAlike any adaptations. The
full legal text and exact model hashes are embedded.

“Alexa” is a trademark of Amazon.com, Inc. The compatibility identifier does
not imply Amazon sponsorship or endorsement.

### Assistant

The assistant payload contains LibreEcho's provider-neutral local assistant
runtime plus curl and Mozilla CA-certificate material. It embeds curl and CA
certificate notices. Provider credentials are never included in release assets.

## Owner-device connectivity firmware — not redistributed

LibreEcho does not publish MT8163 vendor connectivity firmware. At runtime the
device imports required files locally and read-only from the owner's
`system_a`. The importer enforces path, size, and SHA-256 contracts and does not
upload or copy those bytes into public artifacts.

## Amonet/BROM installer integration — external dependency

Installer support remains available, but LibreEcho does not redistribute
Amonet archives, signed Amazon boot-chain partitions, or user-specific wrapper
images. The host-side installer accepts an owner-supplied upstream Amonet input,
credits the upstream Amonet/mtkclient contributors, preserves `system_a`,
userdata and rollback by default, and records only LibreEcho-generated release
artifacts in its SBOM.

## No endorsement

Amazon, Alexa, AirPlay, Apple, Android, MediaTek, and other names are trademarks
of their respective owners. They identify interoperability targets only.
