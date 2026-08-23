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

## Main-branch development prereleases

A successful hosted build caused by a push to `main` uploads one verified run
artifact. `publish-release.yml` consumes that exact run through a
`workflow_run` trigger, checks the product and component source commits,
re-verifies the candidate hashes and independent verifier result, strips the
run down to the boot image plus five feature payload/manifest pairs, and creates
a unique GitHub prerelease tagged `radar-puffin-build-<full-product-commit>`.

These releases are **unsigned development builds**. They contain no signed OTA,
are marked `PREPARED_NOT_FLASHED`, and are not hardware-acceptance evidence.
Pull requests, release-branch pushes, scheduled builds, and manual builds do
not publish releases. The older signed SemVer candidate publisher remains a
separate path.
