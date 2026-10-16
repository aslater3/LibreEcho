# LibreEcho radar-puffin v0.13.11

LibreEcho 0.13.11 is a focused physical-button backport for the Amazon Echo
2nd Gen (`radar_puffin`, ARMv7, Linux 6.1), based on the published 0.13.10
source set. It excludes unrelated 0.14 development changes.

## Button fixes

- Package and start the button daemon and its init service so physical input
  reaches the audio and LED services.
- Restore volume up/down handling and feedback.
- Synchronize physical mute with the kernel-owned privacy latch without a
  second userspace toggle; preserve hardware privacy state during startup.
- Map the action button on `GPIO36` to `KEY_HELP` and provide rotating bundled
  action sounds with LED feedback.
- Persist button preferences, restore them at startup and configuration import,
  and retain mute-ring feedback across temporary effects and LED-service restarts.
- Include the matching PMIC input, privacy-workqueue, device-tree, packaging,
  sound assets, and independent image-verification changes.

Action feedback supports sound rotation or disabling the action. General
listen/play-pause mappings and separate short/long-press actions are not
implemented; legacy compatibility fields do not enable those actions.

## Source and validation policy

- Product release ID: `radar-puffin-v0.13.11`
- Product version: `0.13.11`
- Release impact: `patch`
- Baseline: published 0.13.10 source set, not moving `main`.
- Product, Platform, Linux, and UI use coordinated `release/0.13.11` refs.
  The hosted build must record their exact commits in the candidate manifest
  and `release-source-commits.txt`; UI `VERSION` must equal `0.13.11`.

Merging source is not publication or proof of hardware behavior. Publication
requires component checks, the canonical hosted Product build, independent
image/provenance verification, and separately authorized physical-button,
privacy, boot, rollback, and service-readiness acceptance. Release coordination
and acceptance evidence are tracked in Product issue #134 rather than as
transient status assertions in these reusable release notes.

## Installation and verification

Use only assets attached to the matching published Product release and verify
them against its `SHA256SUMS`. A branch name or source note alone is not an
installable artifact or authorization to install, reboot, or publish.

## License and distribution boundary

Distribution remains subject to the public component allowlist and individual
component notices. The community-noncommercial wakeword payload retains its
**CC-BY-NC-SA-4.0** noncommercial, attribution, modification-notice, and
ShareAlike requirements. TTS voice assets retain their separate
**CC-BY-SA-4.0** obligations. Action sound attribution is retained in the UI
sound provenance notices. Credentials, signing material, device identifiers,
owner-local connectivity firmware, and private build metadata are excluded
from public release metadata.
