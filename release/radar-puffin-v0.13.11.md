# LibreEcho radar-puffin v0.13.11

**Status: UNRELEASED candidate.** This Product metadata records a proposed
0.13.11 patch release for the Amazon Echo 2nd Gen (`radar_puffin`, ARMv7,
Linux 6.1). It is based on the 0.13.10 release line and is intended to carry
the narrowly selected physical-button backport from the 0.14 development work.
It is not a published release, installation recommendation, build result, CI
result, or hardware-acceptance record.

## Release identity and source boundary

- Product release ID: `radar-puffin-v0.13.11`
- Product version: `0.13.11`
- Candidate state: `UNRELEASED`
- Release impact: `patch`
- Backport base: `release/0.13.10`
- Product base identity before this metadata: `66ad65e86d298c55ee10ddebbafc152c74128274`
- Published 0.13.10 baseline source map (provenance only, not 0.13.11
  candidate heads): Product `66ad65e86d298c55ee10ddebbafc152c74128274`,
  Platform `a265f2ee36b9af8fb0591c97c19b4c67b009a45a`, Linux 6.1
  `b110727cdd3dec6ef20c4d28889c63239f0a8ec3`, and UI
  `1d38b3b3318739143171104945c48939edc35bde`.
- Source policy: backport only the selected button functionality; do not merge
  unrelated `main` or `0.14` changes.
- Coordinated Product, Platform, Linux 6.1, and UI source heads must be
  recorded as exact immutable commits before any stable build or publication.

The matching `release/0.13.11` refs and machine-readable UI version are
coordination gates, not assumptions made by this note. The Product workflow
must resolve the exact four-repository source set from the release refs and
fail closed if a component ref is missing or the UI `VERSION` is not
`0.13.11`.

## Candidate scope

This candidate is limited to the physical-button behavior required for the
0.13.11 backport, together with the independently owned Platform, Linux, UI,
and final-device-tree changes needed to close that behavior:

- physical button input and mute/privacy behavior, including the selected
  button daemon and LED/audio feedback integration. The final design must keep
  one owner for the hardware privacy latch, synchronize software mute state,
  preserve mute state across startup, and avoid clearing a preserved hardware
  privacy state with a startup indicator;
- the implemented action-sound feedback path, which rotates through the
  available action sounds. Existing `audiod` cue functionality is preserved;
  this backport must not import unrelated 0.14 regressions; and
- the corresponding PMIC mute, `GPIO36` / `KEY_HELP`, privacy-workqueue,
  packaging, sound, and final-DTB work owned by the component repositories.

The action-sound rotation must not be described as a general action-mapping
settings system. Independent listen/play-pause action selection and
short-press versus long-press action settings are **not implemented by this
candidate**. Legacy compatibility fields may remain, but release notes,
UI controls, API documentation and tests must not present them as working
actions.

No unrelated 0.14 feature, moving `main` head, or broad release-branch merge
belongs in this patch candidate. Component changes remain owned and reviewed
in their respective repositories; Product carries only release-facing metadata
and orchestration for this source set.

## Validation boundary and remaining gates

No build, hosted CI run, signed artifact, device deployment, reboot, physical
button test, or hardware runtime acceptance is claimed by this UNRELEASED note.
Any mute-state or startup-preservation check performed during this metadata task
is only a host/source validation boundary; preservation of the hardware privacy
latch remains unconfirmed until the separately authorized device test.
Before publication, the coordinating release work still requires all of the
following:

1. Create and verify matching `release/0.13.11` refs in Product, Platform,
   Linux 6.1, and UI through the normal pull-request flow; record their exact
   tested heads and prove that none comes from an unrelated `main`/`0.14`
   merge.
2. Confirm UI `VERSION=0.13.11`, Product release-note identity, release branch,
   workflow `release_version`, and candidate manifest agree.
3. Close the button behavior across UI/API, daemon, init/service packaging,
   Platform sounds and final DTB, Linux PMIC mute, `GPIO36` / `KEY_HELP`, and
   privacy-workqueue paths. Compile/source checks do not prove packaged or
   runtime behavior.
4. Run the Product metadata, component, installer/public-input, workflow
   contract, and release-packaging checks, followed by the exact hosted Product
   candidate build and independent image/provenance verification.
5. Confirm the candidate manifest and `release-source-commits.txt` bind the
   exact Product, Platform, Linux, and UI commits; preserve
   `PREPARED_NOT_FLASHED` until a separately authorized deployment decision.
6. Exercise physical button, mute/privacy, LED, action-sound rotation, boot,
   rollback, and service-readiness behavior on authorized hardware. Treat
   listen/play-pause mapping and short/long action settings as out of scope,
   not as failed acceptance criteria for this release.
7. Only after the preceding gates pass may a maintainer perform the separate
   stable workflow dispatch, signing/publication, release-asset checksum
   verification, and any later hardware-acceptance process.

## Installation and publication

There are no 0.13.11 downloads or installation instructions while this note is
`UNRELEASED`. Do not use this candidate identity as a stable tag, `latest`
asset, OTA source, or hardware-test artifact. When authorized publication is
later performed, users must use only the generated release assets and matching
`SHA256SUMS`; publication still does not by itself establish physical-device
runtime acceptance.

## License and distribution boundary

The eventual release remains subject to the repository's public component
allowlist and the individual notices for bundled components. In particular,
the community-noncommercial wakeword payload retains its
**CC-BY-NC-SA-4.0** noncommercial, attribution, modification-notice, and
ShareAlike requirements, while TTS voice assets retain their separate
**CC-BY-SA-4.0** obligations. No credentials, signing material, device
identifiers, owner-local connectivity firmware, or private build metadata
belongs in public release metadata.
