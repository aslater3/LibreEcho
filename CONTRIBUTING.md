# Contributing

## Where changes belong

- Linux 6.1 kernel, device-tree, driver, and kernel-platform changes belong in [LibreEcho-Linux-6.1](https://github.com/aslater3/LibreEcho-Linux-6.1).
- Initramfs, feature packaging, image construction, OTA, and ARM32 product-tooling changes belong in [LibreEcho-Platform](https://github.com/aslater3/LibreEcho-Platform).
- Web UI, API, and daemon changes belong in [LibreEcho-UI](https://github.com/aslater3/LibreEcho-UI).
- Product documentation, installation guidance, and cross-component planning belong here.

For changes spanning repositories, open a product issue here and link the
component pull requests.

## Reports

Before opening an issue, check for an existing report. Include exact steps,
expected and observed behavior, device and software versions, and relevant
logs with secrets removed.

## Branching, pull requests, and versioning

All LibreEcho repositories share one branching model and one version number.
The full rules are in [`AGENTS.md`](AGENTS.md) under "Branching, Pull
Requests, and Versioning"; the essentials:

- Features are developed on `feature/<purpose>` branches cut from `main` and
  merged by PR into the next major release branch `release/X.Y.0`.
- Fixes follow the same flow and merge by PR into the minor release branch
  `release/X.Y.Z`.
- Release branches merge into `main` when release-ready; `main` is never
  committed to directly.
- Release branches are named `release/X.Y.Z` with all three components,
  matching the intended `radar-puffin-vX.Y.Z` product tag. Every repository
  ships the same version for a release.
- PRs must be focused, Conventional-Commit titled, evidence-backed, and
  declare `Release impact:` and `Release note:`. Merging requires explicit
  authorization; see `AGENTS.md` for the strict rules.
