# Contributing

## Where changes belong

- Linux 6.1 kernel, device-tree, driver, and kernel-platform changes belong in [LibreEcho-Linux](https://github.com/aslater3/LibreEcho-Linux).
- Initramfs, feature packaging, image construction, OTA, and ARM32 product-tooling changes belong in [LibreEcho-Platform](https://github.com/aslater3/LibreEcho-Platform).
- Web UI, API, and daemon changes belong in [LibreEcho-UI](https://github.com/aslater3/LibreEcho-UI).
- Product hosted image CI, development prerelease publication, stable release packaging, and GitHub Release publication belong here.

For changes spanning repositories, open a product issue here and link the
component pull requests.

## Development build promotion

Pushes to `main` or `release/**` produce unsigned sandbox artifacts retained for
3 days; routine merges neither publish a prerelease nor move the dev OTA pointer.
The dev channel moves only through an explicit dispatch:
`gh workflow run build-release.yml --ref <main|release/X.Y.Z> -f build_purpose=dev ...`
with the selected ref's required inputs. Stable (`prd`) validation is unchanged.
See [`build/README.md`](build/README.md) for the lane and signing contracts.

Every release follows the cadence in [`docs/RELEASING.md`](docs/RELEASING.md)
(dev build, hardware validation, stable build, documentation sync,
announcement) and is tracked in a *Release checklist* issue.

## Reports

Before opening an issue, check for an existing report. Include exact steps,
expected and observed behavior, device and software versions, and relevant
logs with secrets removed.

## Branching, pull requests, and versioning

All LibreEcho repositories share one branching model and one version number.
The full rules are in [`AGENTS.md`](AGENTS.md) under "Branching, Pull
Requests, and Versioning"; the essentials:

- Features are developed on `feature/<purpose>` branches cut from `main`,
  merge into `main` by PR, and ship in the next feature release
  (`release/0.(Y+1).0`), which is cut from `main` on a regular train.
- Fixes merge into `main` by PR, or into the open release branch they fix;
  they reach `main` through that branch's back-merge. Fix releases
  (`release/0.Y.(Z+1)`) contain fixes only and are cut only when a fix cannot
  wait for the next feature release.
- Breaking changes (OTA format, boot chain, reinstall) ship in their own
  release and never hold back unrelated features.
- Release branches merge back into `main` when final; `main` is never
  committed to directly. One release line is active at a time.
- Release branches are named `release/X.Y.Z` with all three components,
  matching the intended `radar-puffin-vX.Y.Z` product tag. Every repository
  ships the same version for a release.
- PRs must be focused, Conventional-Commit titled, evidence-backed, and
  declare `Release impact:` and `Release note:`. Merging requires explicit
  authorization; see `AGENTS.md` for the strict rules.
