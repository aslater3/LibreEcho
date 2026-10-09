# Multi-target release scaffolding

This cycle packages `radar_puffin` (Echo 2nd Gen) and `biscuit` (Echo Dot 2nd
Gen) at **code parity**. Both use the same ARM32 kernel defconfig, Radar DTB,
userspace, feature payloads, and audio. No Biscuit DTS or tuning is introduced
here.

Each target descriptor's `hardware_accepted` boolean is the maintainer's
acceptance decision for that board. Build metadata (`*-build.json`) copies it
verbatim, and the browser installer installs only builds whose metadata says
`true`. As of 0.14.0 both targets are set to `true` by maintainer decision.

## Target authority and build selection

`release/targets/<target_id>.json` is Product's target authority, constrained by
`release/target.schema.json`. Run:

```sh
python3 build/ci/validate-targets.py --targets radar_puffin,biscuit
build/ci/build-public-release.sh --targets radar_puffin,biscuit --no-publish
# Existing single-target callers still default to Radar:
build/build.sh --target radar_puffin --no-publish
```

The hosted `targets` input defaults to `radar_puffin`. Unknown, empty,
whitespace-padded, and duplicate selections fail closed. The descriptor digest
is SHA-256 over UTF-8 JSON with sorted keys and separators `(',', ':')`, without
an ending newline. New candidate records and prepared `build.json` bind
`board=<target_id>` and `target_descriptor_sha256`.

The build compiles the kernel and shared userspace/features once, snapshots
those inputs, and packages/verifies each target independently. Target-sensitive
image, OTA, feature-plan, handoff, and candidate outputs have separate run
roots. The legacy `CURRENT` remains Radar-only. Combined preparation checks
source-commit equality and byte equality of all kernel/DTB/config/feature
inputs before producing one release.

Product feature-detects the pinned Platform tools' `--target` support. An old
Platform checkout is usable only for a Radar-only selection. Biscuit or a
combined build fails with a coordinated-Platform dependency message before
compilation. Image builders also need `--target-descriptor-sha256`.

## Combined publication and recovery assets

Tags stay `radar-puffin-build-…`, `radar-puffin-nightly-…`, or
`radar-puffin-vX.Y.Z`. All existing Radar asset names remain; Biscuit release
assets use `libreecho-biscuit-…`. Each target retains its independently verified
checksum inventory and target-bound initial-install manifest and signed OTA.

The publisher invokes the pinned Platform `build_install_bundle.py --target`
on an isolated, checksum-verified asset set for each target. It emits:

- `libreecho-radar-puffin-install.zip` and
  `libreecho-radar-puffin-bundle.manifest`;
- `libreecho-biscuit-install.zip` and `libreecho-biscuit-bundle.manifest`;
- `libreecho-<combined-tag>-<slug>-TWRPINSTALL-SHA256SUMS` per target;
- Radar byte-copy aliases `libreecho-install.zip` and `bundle.manifest`, plus
  the existing Radar `<release-prefix>-TWRPINSTALL-SHA256SUMS` alias inventory.

Asset counts are derived from the actual assembled directory. The existing
Radar dev pointer remains Radar-only. The Biscuit pointer publisher is behind
`LIBREECHO_PUBLISH_BISCUIT_DEV_POINTER=true` in workflow variables; unset is off.

OTA v2 needs a prior, verified **same-target** feature catalog. Product will not
reinterpret a Radar baseline as Biscuit. Until a Biscuit baseline exists, its
first-install scaffolding can be exercised using explicit OTA v1, subject to
any stricter release-completeness gate introduced by the coordinated release
work. Device-migration baselines are single-target; the hosted migration lane
is still Radar-only.

### Integration with release-completeness PR #199

The recovery adapter forwards each isolated target set and its target-qualified
bundle manifest to `release_completeness.check_assets(..., target=...)` when
that module is present. It never copies or reimplements the completeness gate.
If that module does not support the selected target, publication fails closed.

The #199 gate's target registry, provenance basename, plan validation,
stage/ship/check-run functions, and recovery inventory recording still need
coordinated target plumbing. Its existing Radar-only defaults must not be
silently bypassed. The draft PR records the local test-merge conflicts and this
integration dependency.

## Installer identity and the expdb operator decision

Stock fastboot `product` maps `RADAR` to `radar_puffin` and `BISCUIT` to `biscuit`.
A cross-flashed LK identifies its donor boot chain, **not** the physical board.
Use explicit `--target radar_puffin` or `--target biscuit` for a cross-flashed
LK; mismatches are refused before any write. Saved transactions bind their target,
so continuation cannot silently select another target's bundle.

The one-shot installer no longer uses the legacy k32 BROM path. It starts from
stock fastboot and never erases `expdb`. On v2 Kaeru, expdb contains the
LK-stage payload, which is the unlock proof, so it must stay intact.

Each target has its own pinned community Amonet ZIP, recorded in
`release/amonet-pins.json`. Biscuit accepts any LK build: its reviewed build
`63cb91b-20221007_072309` uses its own payload, and any other build uses the archive's
default `fastbrick.img`, as upstream `fastbrick.sh` does. Radar accepts its three
reviewed builds (`59779ca-20220524_183401`, `63cb91b-20221007_072309`,
`63cb91b-20221007_073612`) and refuses any other. Payloads are
selected by exact `lk_build_desc` and verified by size and SHA-256 before any
write.
Deleting or retargeting that erase without a reviewed boot-chain decision is
not part of this scaffolding. No USB/hardware validation or release publication
is implied by host tests.
