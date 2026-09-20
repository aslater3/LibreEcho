# LibreEcho radar-puffin v0.13.19

A focused patch release that fixes fresh installs silently stopping after a few reboots (LibreEcho#180, the same failure class as #28) and adds boot-slot diagnostics for troubleshooting.

## Fresh installs no longer expire their only boot slot (LibreEcho#180, Platform #60)

- A fresh install previously confirmed its boot slot only if the complete production service graph came up. A single failed optional service left the slot unconfirmed, and the bootloader refuses a slot whose retry count reaches zero, so the device stopped booting with no ADB.
- The first-boot confirmation is now gated on the boot/recovery plane only (persistent data, ADB control plane, web service) held across three health probes. The complete service graph remains the stricter OTA acceptance gate, where a confirmed fallback slot exists.
- The initial-install installer verifies the running boot slot over ADB after staging and confirms it when the device has not, gated on the reviewed boot/recovery preflight. A refusal is reported with the raw bootloader state and never silently skipped.
- Every boot records the boot-control state to persistent storage: a monotonic boot counter, the per-slot bootloader priority/tries/success, the confirmation verdict and the reason no confirmation was attempted, plus a bounded per-boot history carrying the previous boot's verdict.
- The web control centre reports the boot-slot confirmation state and remaining bootloader retries (`GET /api/v1/system/boot` and a Boot slot panel), and the diagnostic bundle now carries the boot-control record, so a device that is still booting can produce the evidence that explains why it stopped.

## Installer

- Collects `libreecho-bootctl status`, the boot-health/boot-history/boot-count records, the update-record listing and the `libreecho-init` verdicts from the kernel ring in the install evidence archive.

## Experimental status and validation boundary

This release addresses the reported no-longer-starts failure, but it has **not** been confirmed on hardware: the reporter's exact failing health probe was never identified, and no device has flashed a 0.13.19 build yet. Early testers are the validation. Before installing, understand that a device that cannot reach the boot/recovery plane (data partition, ADB, web service) will still stop booting, and that the boot-slot records are destroyed by a reinstall that formats userdata.

## Coordinated source set

- Product version: `0.13.19`
- Product release ID: `radar-puffin-v0.13.19`
- Release impact: `patch`
- Baseline: exact coordinated source commits recorded by the hosted 0.13.18 build, not the 0.14 feature line.
- Product, Platform, Linux and UI use `release/0.13.19`; UI `VERSION` equals `0.13.19`. Linux has no runtime changes for this release.

## Release status and validation

These notes describe source preparation, not publication or physical hardware acceptance. Before publishing, merge the linked component fixes, run the canonical hosted build with all four `release/0.13.19` refs, verify the signed complete asset inventory and independent image/provenance checks, and record live installation and repeated cold/warm boot acceptance confirming the running slot is confirmed (`slot_X_success=1`). Do not reuse or replace the published 0.13.18 assets.

---

## Generated exact-source ledger

- Release channel: `stable`
- Release classification: normal GitHub release
- Product, Platform, Linux 6.1, and UI identities are bound to the exact stable build artifact.

## Cross-repository included changes

The following ledger is generated from the exact component heads used by the stable image. It is not inferred from branch names.

### Product

- Selected head: filled by the hosted build for the selected `release/0.13.19` Product head.
- Changes: installer boot-slot verification and confirmation with boot-slot evidence capture; protected OTA signing bound to 0.13.19.

### Platform

- Selected head: filled by the hosted build for the selected `release/0.13.19` Platform head.
- Changes: first-boot slot confirmation gated on the boot/recovery plane; boot-count, boot-health and boot-history recorded to userdata every boot.

### Linux 6.1

- Selected head: `release/0.13.19`.
- Changes: none in this release.

### UI

- Selected head: filled by the hosted build for the selected `release/0.13.19` UI head.
- Changes: `GET /api/v1/system/boot` and the Boot slot settings panel; boot-control section in the diagnostic bundle.

## Downloads and verification

The release contains the signed OTA bundle, initial-install bundle, boot image, feature payloads and manifests, OTA public key, installer, and `SHA256SUMS`. Verify the published checksum inventory before use.

## License and distribution boundary

This release is `community-noncommercial`. The wakeword model is licensed under **CC-BY-NC-SA-4.0**: use is noncommercial, attribution is required, modifications must be indicated, and adaptations remain subject to **ShareAlike**.

TTS voice assets include material under **CC-BY-SA-4.0** and retain their separate attribution and ShareAlike obligations.

Review the bundled notices and the project release-closure records before redistribution. The release excludes credentials, signing keys, device identifiers, owner-local connectivity firmware, and vendor boot-chain material.

## Validation boundary

Stable publication proves that the Product workflow built, signed, and verified the release asset set. It does not by itself claim physical-device runtime acceptance; deployment, readback, runtime validation, and slot confirmation are separate evidence gates.
