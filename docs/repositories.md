# Repository Boundaries

LibreEcho is intentionally split into two implementation repositories and one
product-home repository.

| Repository | Owns |
| --- | --- |
| `LibreEcho` | Product documentation, support, roadmap, cross-component issues, release notes, and signed OTA release assets |
| `LibreEcho-Kernel` | MT8163 kernel, initramfs, hardware bring-up, image construction, OTA verification, and release workflow |
| `LibreEcho-UI` | Web control centre, HTTP API, service daemons, and UI tests |

Keep product-level reports in this repository. When implementation work is
needed, link the resulting component pull requests from the issue. Put narrow
implementation regressions directly in the owning component repository when
they would not be useful to users of the whole product.

Recommended labels across repositories include `component:kernel`,
`component:ui`, `component:ota`, `component:hardware`, `type:bug`,
`type:feature`, and `type:documentation`.
