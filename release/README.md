# LibreEcho public release closure

This directory contains public, hardware-agnostic release metadata and the
checks that generate it. It must never contain a per-device deployment record,
private build manifest, signing key, Wi-Fi configuration, vendor firmware,
Amonet/BROM wrapper image, calibration data, or local filesystem path.

## Release boundary

A complete `community-noncommercial` release has two layers:

**Normal public downloads** contain:

- a signed OTA archive;
- one initial-install bundle containing the boot image, feature payloads, and
  manifests; and
- a checksum file covering those user-facing artifacts.

**Request-based compliance materials** are retained privately and furnished to
recipients on request:

- the corresponding-source and relink offers for the exact candidate;
- `THIRD_PARTY_NOTICES.md` and embedded payload notices;
- the generic release provenance; and
- the SPDX SBOM for the exact assembled artifacts.

The private request-based materials are part of the release-closure record even
though they are not normal public download assets. The `community-noncommercial`
scope includes all five payloads and is cleared only when the CC-BY-NC-SA-4.0
model restriction, attribution, ShareAlike terms, and exact source/relink offer
are retained and can be furnished to recipients. Neither scope is a publication
or hardware-acceptance claim by itself.

The four MT8163 connectivity firmware files are **owner-device-local inputs**.
They are never stored in this repository, CI artifacts, OTA archives, or GitHub
Releases. An installer may extract them locally from an owner device only after
identity/path/hash checks and must never upload them.

## Fail-closed component policy

`components.json` is an allowlist, not a list of suggestions. Redistributed
`source-release`, `core-image`, and `separate-payload` records must have
`release_status: cleared`, an SPDX expression, a public download location, a
source offer, and public evidence. A component with `allowed_release_scopes` is
included only in those named scopes. Owner-local or user-supplied dependencies
must use `release_status: not-redistributed` and are omitted from redistributed
SPDX packages.

The wakeword payload is deliberately separate and remains subject to
CC-BY-NC-SA-4.0, including its noncommercial and ShareAlike conditions. The TTS
voices remain separate CC-BY-SA-4.0 assets. These restrictions are presented in
the release notes and embedded legal text; they are not hidden behind the MIT
license used by LibreEcho-authored product code. Do not describe a
`community-noncommercial` release as permitting unrestricted commercial reuse.

Amonet support remains an external user-supplied integration. LibreEcho does not
publish upstream Amonet archives, signed vendor boot-chain partitions, wrapper
images, or owner-device connectivity firmware.

## Generate a release record

The private build host supplies the candidate and artifacts; only sanitized
output is copied into this repository or attached to a release:

```sh
python3 tools/prepare-release.py \
  --candidate /private/run/CURRENT.candidate \
  --artifact boot.img \
  --artifact ota.tar \
  --release-id radar-puffin-vX.Y.Z \
  --product-commit <40-hex-commit> \
  --kernel-commit <40-hex-commit> \
  --tooling-commit <40-hex-commit> \
  --ui-commit <40-hex-commit> \
  --release-scope community-noncommercial \
  --output-dir /tmp/libreecho-release
```

Generate the SPDX 2.3 inventory from the same sanitized provenance, component,
and artifact records. The provenance release ID and scope must match and its
source commits replace the `provenance:<source>` versions before SPDX package IDs
are calculated:

```sh
python3 tools/prepare-sbom.py \
  --release-id radar-puffin-vX.Y.Z \
  --created <UTC-ISO-8601> \
  --release-scope community-noncommercial \
  --components release/components.json \
  --provenance /tmp/libreecho-release/radar-puffin-vX.Y.Z-provenance.json \
  --artifacts /tmp/libreecho-release/artifacts.json \
  --output /tmp/libreecho-release/radar-puffin-vX.Y.Z.spdx.json
```

Redistributed component records contain
exact or provenance-bound versions, SPDX license expressions, public download
locations, source offers, and required notices. `NOASSERTION` is accepted only
for non-redistributed dependencies or a separately documented good-faith
exception with an exact provenance and hash contract.

The command fails closed when the candidate is dirty, contains embedded vendor
files, identifies a device, or includes a component that is not cleared.
Artifact paths are used only to calculate hashes and are never written to the
output. Rename private run-generated artifacts to stable public-safe filenames
before invoking the generator; run IDs are rejected in public artifact names and
release metadata.

Do not use a generated per-run `manifest.json` as a public release manifest.
It contains host paths and operational fields by design.
