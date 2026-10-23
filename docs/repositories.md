# Repository Boundaries

LibreEcho is intentionally split into three public component repositories and
one product-home repository. Private build/release orchestration combines
explicit, pinned versions of those components without changing their ownership
boundaries.

| Repository | Owns |
| --- | --- |
| `LibreEcho` | Product documentation, support, roadmap, cross-component issues, release notes, and signed OTA release assets |
| `LibreEcho-Platform` | ARM32 product tooling, initramfs, feature packaging, OTA verification, and release workflow; historical 3.18 compatibility remains here |
| `LibreEcho-Linux` | MT8163 Linux 6.1 kernel on upstream stable history, with kernel-side platform changes and the in-tree connectivity stack |
| `LibreEcho-Linux-6.1` | Archived. Kernel source for releases up to 0.14.4; replaced by `LibreEcho-Linux` |
| `LibreEcho-UI` | Web control centre, HTTP API, service daemons, and UI tests |

Keep product-level reports in this repository. When implementation work is
needed, link the resulting component pull requests from the issue. Put narrow
implementation regressions directly in the owning component repository when
they would not be useful to users of the whole product.

Recommended labels across repositories include `component:kernel`,
`component:ui`, `component:ota`, `component:hardware`, `type:bug`,
`type:feature`, and `type:documentation`.
