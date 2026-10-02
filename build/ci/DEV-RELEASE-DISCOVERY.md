# Development OTA discovery

The publisher requires an explicit purpose in the exact successful build
artifact's release request; missing purpose fails closed. Manual runs use
`build_purpose` (default `sandbox`), not `update_channel`:

| Purpose | Channel | Publication / retention |
| --- | --- | --- |
| `sandbox` | dev | Never publishes or moves discovery; artifact expires after 3 days. Optional `sandbox_signing=signed`, otherwise unsigned. |
| `dev` | dev | Signed prerelease + dev discovery pointer; artifact retained 7 days. Dispatch on `main` or `release/**`. |
| `prd` | stable | Signed stable release from matching `release/X.Y.Z`; artifact retained 7 days. |

PRs and release-branch pushes derive sandbox; main pushes derive dev. Dev/prd
are always signed and reject `sandbox_signing=signed`. Scheduled builds derive
unsigned sandbox, keeping Actions caches warm with 3-day artifacts; the publisher
rejects schedule events before downloading artifacts.
Sandbox requests record `publish=false`; dev/prd record `publish=true`.
Stable requests retain manual matching-release-branch and stable metadata gates.

After a complete signed development build (never a scheduled sandbox) is
published and its asset names, sizes, and SHA-256 digests match the verified
preparation, the publisher advances
`radar-puffin-dev-channel/release-pointer.txt`. This prerelease is a mutable
transport pointer, not a source tag or a signing authority. It contains exactly
an immutable `radar-puffin-build-*` tag and the SHA-256
of that release's canonical OTA, one per newline-terminated line. Only this
pointer asset is replaced. Source tags, signed OTAs and external assets remain
immutable; stable/latest is never used or changed for dev discovery.

Dev publication is serialized across source branches. An upload replacement can
briefly return 404; clients fail closed and retry through their normal check
path. The fetcher bounds pointer reads, resolves it once, verifies the downloaded
control against the pointer and installed signature trust root, and downloads
external assets from the same immutable tag using signed names, sizes and hashes.
Unsigned dev artifacts never advance the OTA pointer.

Existing images with the old hardcoded latest-stable dev URL need a signed
bridge containing the new fetcher, delivered through authenticated OTA upload.
Changing ota-source.conf alone does not repair those binaries. Baseline/preserve
checks remain mandatory, including for development-device migration packages.

The `workflow_run` publisher must also be integrated into the default branch
before a release-branch merge can change live downstream publication behavior.
Its YAML runs from `main`, while routing/preparation scripts are checked out from
the build commit: purpose derivation and routing must ship together.
Build success, publication readback, and device installation are separate gates.
