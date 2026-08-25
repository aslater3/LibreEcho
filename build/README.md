# Hosted public build boundary

The public workflow is GitHub-hosted only. It does not use `LibreEcho-Build`, a
self-hosted runner, Vaultwarden, sudo, private dependency roots, local caches,
private models, or owner-local firmware bytes.

`build/inputs/public-inputs.json` is the closed dependency inventory. Entries
must have public HTTPS URLs, exact SHA-256 digests, licenses, and cleared
redistribution status before the hosted dependency job may fetch them. Pinned
`source-git` entries additionally require an exact 40-hex commit and the
`source-git-pinned` redistribution marker. Current unresolved toolchain entries
are deliberately blocked; this branch must not fall back to local copies. The
device-local connectivity firmware is represented only by the public importer
contract: first boot verifies and copies it from stock `system_a`; the bytes
never enter CI, Git, or release assets.

The OTA signing job uses the reviewed Python 3.11/Linux wheel closure under
`build/inputs/reviewed/python-wheels/`. `PyNaCl==1.5.0`, `cffi==1.17.1`, and
`pycparser==2.22` are each vendored and SHA-256-pinned in the public inventory;
`requirements.txt` is pinned as well. The build artifact verifies its complete
`SHA256SUMS` before installing with pip `--no-index`, `--find-links`, and
`--require-hashes`. No OTA signing dependency is fetched from the live package
index, and this verification runs before the protected signing key is
materialized.

`build/ci/build-public-release.sh` now invokes the preserved mature builder with
only explicit hosted dependency/source roots. It fails before compilation when
the generated public ARM32 toolchain or any required source root is absent; it
never falls back to local paths.

The hosted image job builds the neural dependency closure after installing the
public ARMHF compiler/sysroot with `build/ci/build-public-neural-deps.sh`. It
checks out ONNX Runtime v1.27.0 and Sherpa-ONNX at the exact inventory pins,
builds static ARM32 ONNX Runtime and Sherpa libraries, retains the FlatBuffers
Python generator tree, builds RE2 and SpeexDSP, and writes only the generated
roots under the job-local `public-deps/neural/` directory. The mature builder
receives those roots explicitly; it never discovers or imports a private neural
cache. The reduced wakeword ONNX Runtime build remains a separate run-local
step performed by the mature builder from the same pinned ONNX Runtime source.

## Issuing a stable release

Use GitHub only: open **Actions → Hosted LibreEcho build and release → Run
workflow**, select the matching `release/X.Y.Z` branch, choose `stable`, and
provide the version plus the reviewed Amonet repository/tag/commit. The
workflow generates release notes through GitHub's release-notes API, builds and
signs the exact artifact, waits for the protected signing environment, and
publishes the normal `radar-puffin-vX.Y.Z` release. No local release command or
checked-in release-notes file is required.

## Product release lanes

The Product repository owns all hosted image and release automation. The lanes
are deliberately separate:

- **PR validation:** pull requests targeting `main` or `release/**` build a
  no-publish OTA-profile image with the development channel. The run artifact is
  validation evidence only and never publishes a release.
- **SSH option:** all non-dispatch builds keep SSH disabled. A manual GitHub
  Actions run may select `ssh_enabled=enabled`; that run requires the protected
  `LIBREECHO_SSH_ROOT_PASSWORD_HASH` secret and embeds the static ARM32 Dropbear
  server plus `dropbearkey` in the initramfs. The image uses password-only root
  login, does not include public-key authorization or persistent host keys, and
  records both binary hashes in the release manifest.
- **Development:** pushes to `main` publish a bounded signed `dev`-channel GitHub
  prerelease from the exact workflow artifact. Pull requests remain unsigned
  validation-only builds. These releases are `PREPARED_NOT_FLASHED`, are not
  marked latest, and are not hardware-acceptance evidence.
- **Nightly:** the scheduled `main` run uses the same no-publish signed build but tags
  its output as `radar-puffin-nightly-*`. After successful publication it keeps
  only the three newest nightly prereleases and removes older nightly releases
  and tags. Ordinary development prereleases are not affected.
- **Stable/product:** a maintainer manually dispatches the workflow from a
  matching `release/X.Y.Z` branch with `update_channel=stable`, the release
  version, release notes, and the reviewed Amonet tag. The protected
  `stable-release` environment supplies `LIBREECHO_OTA_SIGNING_KEY_HEX`; the
  image job uses local signing and must produce exactly one signed OTA bundle.
  The following Product `workflow_run` prepares and publishes the exact stable
  artifact set as the normal `radar-puffin-vX.Y.Z` GitHub release.

Stable publication is fail-closed on the exact Product workflow artifact,
source identities, OTA hash, public key, feature payload hashes, installer
bundle, release notes, and complete `SHA256SUMS`. It does not flash hardware or
claim runtime acceptance. Configure the protected environment secret and
approval rules before attempting a stable dispatch.
