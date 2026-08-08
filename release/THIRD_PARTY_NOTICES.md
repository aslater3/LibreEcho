# LibreEcho third-party notices

This file records the release boundary. It is not a blanket license for every
file produced by the private build pipeline. Each component retains its own
license and redistribution conditions.

## LibreEcho

LibreEcho product source is MIT-licensed; see the repository `LICENSE`.

## Linux

The Linux kernel and kernel-side product changes are covered by the Linux
kernel's GPL-2.0-only terms and their in-tree notices. The corresponding source
for the exact release commit must accompany any binary release.

- Source: <https://github.com/aslater3/LibreEcho-Linux-6.1>
- Upstream: <https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git>

## AOSP adbd

The ARM32 `adbd` replacement is built from the pinned AOSP
`platform/system/core` source with the project compatibility patch. It is
Apache-2.0 licensed. The exact source commit, patch, and AOSP `NOTICE` must be
included in the release source/provenance closure.

- Source: <https://android.googlesource.com/platform/system/core>

## BusyBox and wireless networking

BusyBox and the wireless networking stack are separate third-party components.
Their exact versions, source archives, license texts, and corresponding-source
records must be attached to a release. They are not covered by the LibreEcho
MIT license.

## AirPlay runtime

The AirPlay payload includes Shairport Sync, nqptp, Avahi, D-Bus, crypto,
libplist, and other runtime dependencies. A public release must include the
exact dependency inventory, license texts, notices, modifications, and
corresponding-source/source-offer links required by each dependency. Research
acknowledgements do not substitute for these obligations.

- Shairport Sync: <https://github.com/mikebrady/shairport-sync>
- nqptp: <https://github.com/mikebrady/nqptp>
- Avahi: <https://github.com/lathiat/avahi>
- D-Bus: <https://www.freedesktop.org/wiki/Software/dbus/>

## Speech and models

The STT/TTS runtimes and their model files are separate release objects. Model
cards, exact immutable model identities, voice/data provenance, license terms,
and runtime dependency notices must accompany them.

- Sherpa-ONNX: <https://github.com/k2-fsa/sherpa-onnx>
- ONNX Runtime: <https://github.com/microsoft/onnxruntime>
- openWakeWord: <https://github.com/dscripka/openWakeWord>

The currently audited wakeword model declares `CC-BY-NC-SA-4.0`. It is not
cleared for an unrestricted public release and must be replaced by a model and
training data with compatible redistribution terms before enabling wakeword in a
public full-feature release.

The current TTS voice/model closure is incomplete. Do not infer permission from
the runtime license or from the existence of a model download; voice identity,
training data, model card, and redistribution terms must be verified separately.

## Device-local connectivity firmware

The MT8163 WMT/Wi-Fi firmware files are copied only from the owner's read-only
`system_a` partition after exact path, size, and SHA-256 checks. They are not
included in the public source, boot image, OTA archive, CI artifacts, or GitHub
Release. The installer must never upload extracted bytes.

## Installer and hardware research

Amonet/BROM tooling and reverse-engineering research are not automatically part
of the OS release. If an installer is published, it needs its own provenance,
license, source, and safety review. Do not imply manufacturer affiliation or
endorsement.

LibreEcho is an independent project for hardware owned or authorised by the
operator. Firmware modification can cause data loss, device damage, or loss of
recovery access. Users are responsible for backups and compliance with the
licenses and laws applicable to their device and region. This project provides
no manufacturer warranty or endorsement.
