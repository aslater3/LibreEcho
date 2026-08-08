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
- **BusyBox 1.37.0-r30** — GPL-2.0-only, Alpine packaging commit
  `1e823a60eb85606954b3a5af5f8e5bbd1ea680cf`. The public source offer includes
  the upstream source, Alpine patches/APKBUILD, and complete configuration
  exported by the exact shipped binary.
- **musl 1.2.5-r21** — MIT.
- **`wpa_supplicant` 2.10** — BSD-3-Clause; the binary's complete `-L` notice is
  embedded.
- **wireless-tools, wireless-regdb, TinyALSA, libsodium, glibc, and GCC runtime
  code** retain their GPL, ISC, BSD, LGPL, and GCC Runtime Library Exception
  terms. Exact versions and notices are embedded in the core image.

### MT8163 audio FPGA bridge

`i2s_to_spi_v34.bin` is retained because it is required by the working audio
path. The release records its exact 30,964-byte identity:

`77a558bacdaaf9e343f02f2d74f27a5f2bb2dc8b6d66cc2499b60ed14ef62fe6`

The identical file is independently published in the Amazon-device Linux 3.18
kernel source lineage at commit
`5b48c78b249ed9129fe92d30087de25b20152538`, distributed with that kernel's
GPL-2.0 `COPYING`. Credit belongs to the Amazon/MediaTek device-kernel
contributors. LibreEcho preserves the bytes unchanged and claims no original
authorship.

## Separate feature payloads

The following SquashFS files are independent release assets. Users may install
all of them, but each preserves its own license conditions.

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
