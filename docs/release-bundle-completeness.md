# Self-contained release bundles

A release must be usable without another device's installed feature tree. OTA
v2's changed-only `feature-assets.json` is **not** an installation inventory:
`preserve` still requires its signed base payload and base manifest, and bases
for `replace`/`runtime` remain signed references too.

## Build and publication gates

`build/ci/release_completeness.py` keeps the existing signed plan and OTA target
inventory unchanged. After the image build, before uploading the release build
artifact, it:

1. Copies every base pair from the verified prior-release catalog, preserving
   the catalog's source release tag and original asset name. For an explicit
   device baseline, it resolves each signed digest against published GitHub
   release asset metadata, with bounded pagination/downloads and independent
   SHA-256 and size verification. Missing GitHub digests, unpublished sources,
   unavailable bytes, or identity mismatches fail the build.
2. Stages bases in `release-bases/` as
   `libreecho-radar-puffin-base-<feature>-<sha256>.payload.squashfs` and
   `libreecho-radar-puffin-base-<feature>-<sha256>.manifest.json`. Original
   manifest bytes are never renamed internally or rewritten.
3. Writes `libreecho-radar-puffin-release-completeness.json`. Its schema is
   `libreecho-release-completeness-v1`, its `target` is `radar_puffin`, and each
   reference records `feature_id`, `role` (`base`/`target`), `kind`, shipped
   `name`, `size`, `sha256`, `source_release`, and `source_asset`. Targets use
   `current-build` only in the build artifact; assembly replaces that with the
   actual published release tag. There are no workstation paths in this file.
4. Rechecks the anchored Ed25519 signature and signed plan/inventory binding,
   exercises the verified Platform recovery builder on the complete bytes, and
   checks every `bundle.manifest` pin plus exactly one correct `staging=` pair
   for each feature. Preserve stages its base pair; replace stages its target.

Development/nightly and stable assembly ship these bases and provenance as
checksum-covered release assets. Their initial-install tars select the signed
preserved base rather than the newly built, unselected candidate. Existing
candidate and OTA asset names remain published unchanged. The shipped host
installer accepts only the explicitly plan-bound base namespace and checks all
release checksums, while retaining support for legacy releases without this
additional metadata.

Both publication lanes run the signature/asset/staging completeness check again
after assembling release assets and the TWRP bundle, before any publication.
Stable assembly records its recovery zip and manifest in both exact publisher
inventories. No signatures or hash checks are weakened.

## Fail-closed boundaries

The current Platform recovery installer supports `preserve` and `replace`, not
`runtime`. A runtime plan is refused before release artifact upload; simply
shipping a runtime capsule does not make its base/overlay installable. Legacy
v1/unsigned builds have no signed v2 recovery feature contract and cannot pass
this release gate either. Supporting those fresh-install paths requires a
separately reviewed Platform contract; do not omit the gate to publish them.
Existing OTA-only runtime validation/packaging tests remain intact, but are not
proof that the fresh-install publication gate will accept a runtime release.

No Platform edit is needed for a preserve/replace release: its current recovery
builder uses the corrected initial-install manifest, and its installer already
finds preserved bytes by digest. Recommended Platform defense-in-depth is to
resolve preserve pairs directly from signed base hashes in the builder and
preflight that closure **before** formatting userdata or writing boot slots;
the preserve manifest copy should also be mandatory and read back by digest.

## Forward-compatible naming boundary

Future multi-target releases will be **one combined release per product
version**, with target-namespaced assets, per-target recovery zips, and
per-(target, channel) pointers. New base/provenance names already include the
`radar-puffin` slug. The gate iterates its supported target inventory and checks
the signed board/provenance target; only `radar_puffin` is implemented today.
This change does not add another target, change discovery pointers, or rename
the existing single-target recovery assets.

## Host validation

Run `build.tests.test_release_completeness` with the reviewed Python 3.11 wheels
and `LIBREECHO_PLATFORM_SRC`/`LIBREECHO_PLATFORM_SOURCE` pointing to the verified
Platform source. This suite is in the build workflow's real unit-test list. It
covers all missing reference classes, corrupt/unsafe assets, provenance,
incorrect/missing recovery staging, digest resolution, the bb79646 preserve
shape, and real signed development/stable assembly plus the Platform builder
and standalone host installer. Host packaging checks do not imply image boot,
hardware acceptance, or permission to publish or flash.
