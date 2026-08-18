# BROM Inactive-Slot LibreEcho Installation Design

## Status

Approved for implementation on 2026-08-17. The first live operation must be
inventory-only. Installation remains disabled until the inventory output and
backups have been reviewed.

## Objective

Create a recovery-safe, BROM-native path that deploys the signed LibreEcho
development OTA boot image to one redirected Amonet payload slot on an Amazon
Echo 2nd generation (`radar_puffin`, MT8163). The first milestone ends when the
device boots the release initramfs and exposes a working ADB shell over USB.

Feature payloads, Wi-Fi firmware, and product configuration are explicitly
deferred until ADB works.

## Context

The public release does not contain the initial-install bundle required by the
installation guide. The current device has already passed through the Amonet
K32 flow and has the modified 18-partition Biscuit GPT:

The expected disk GUID is `081BEED7-DF86-4A71-864E-435385BA18D9`.

| Purpose | GPT name | Start LBA | Sectors |
| --- | --- | ---: | ---: |
| A redirected image | `boot_a_x` | 163840 | 32768 |
| B redirected image | `boot_b_x` | 196608 | 32768 |
| A Amonet wrapper | `boot_a` | 7183360 | 225280 |
| B Amonet wrapper | `boot_b` | 7408640 | 225280 |
| Boot control | `misc` | 118784 | 1025 |
| Fastboot marker | `expdb` | 98304 | 20480 |
| Persistent data | `userdata` | 5046272 | 2137088 |

The 110 MiB `boot_a` and `boot_b` partitions are Amonet wrapper invariants.
The actual 16 MiB Android boot images belong in `boot_a_x` and `boot_b_x`.

The current Amonet installer source appears to write an Android-style `BCb`
record where the LibreEcho Platform and Amonet boot-control library require a
seven-byte Amazon `ABB` record. The live BCB must therefore be treated as
unknown until read from the device. Inventory must not repair or reinterpret
an invalid record.

## Release Input

The only accepted first-boot input is the published development-channel OTA:

- Filename: `libreecho-radar-puffin-dev.ota.tar`
- Archive SHA-256:
  `5eb210077527449fc831b88bc0776022b72538a635c76720c9f01e781af58a3f`
- Exact members, in order: `manifest`, `manifest.sig`, `boot.img`
- Embedded boot image size: 16777216 bytes
- Embedded boot image SHA-256:
  `5ca302c958c1449a569db646b4b743ae5be5baaf8ec58a2eb86161ab1c286e15`

The installer must verify the manifest's Ed25519 signature using the published
OTA public key. It must also require these manifest values:

```text
format=libreecho-ota-v1
manifest_version=1
board=radar_puffin
soc=mt8163
architecture=armv7
boot_filename=boot.img
boot_size=16777216
feature_policy=community-noncommercial
image_profile=ota
service_profile=production
update_channel=dev
```

It must validate the Android boot magic and compare the extracted image hash
with both the signed manifest and the pinned expected hash.

## Architecture

### Launcher

A shell launcher in `/home/lucaspick/Downloads` on nixtop provides the operator
entry point. It:

1. Confirms the active user and `dialout` membership.
2. Creates the temporary Nix Python/PySerial environment.
3. Verifies pinned hashes for the reused Amonet payload and transport modules.
4. Runs offline tests and the existing Amonet preflight.
5. Waits only for MediaTek BROM USB ID `0e8d:0003` and refuses Preloader
   `0e8d:2000`.
6. Starts the Python tool with either `--inventory-only` or `--install`.

### Python core

The Python implementation separates pure validation and transaction planning
from the live Amonet transport. Pure functions accept byte strings, parsed GPT
records, and manifests so their behavior can be exhaustively tested without a
device.

The live layer reuses only the verified Amonet `Device`, handshake, payload
loader, and GPT parser. It must never invoke Amonet's full installer entry
point.

### Artifacts on nixtop

The installer, tests, launcher, README, downloaded OTA, and per-device backup
directory live under `/home/lucaspick/Downloads`. Backups use a timestamped
directory and are never uploaded.

## Inventory-Only Transaction

Inventory is the mandatory first live run and has no write-capable code path.
After the BROM handshake and payload upload, it performs these steps:

1. Switch to the eMMC user area.
2. Read and validate the primary GPT, backup GPT, disk GUID, partition count,
   exact names, start LBAs, and sizes.
3. Save the primary and backup GPT sectors.
4. Save the complete `misc` partition.
5. Save complete 16 MiB images from `boot_a_x` and `boot_b_x`.
6. Read and save the first and last sectors of `boot_a` and `boot_b`.
7. Compute evidence hashes for both full wrapper partitions by streaming reads
   to the host. Full wrapper bytes do not need to be retained if the stream
   hash succeeds, but retaining them is permitted when disk space allows.
8. Save the first `expdb` sector.
9. Decode, but do not modify, the seven BCB bytes at absolute `misc` offset
   `0x360`.
10. Write a machine-readable JSON report and a human-readable summary.

The report contains geometry, hashes, BCB bytes and decoded fields, Android
boot headers, wrapper boundary bytes, and tool identities. It must not contain
device serial numbers, MAC addresses, Wi-Fi credentials, private keys, or
calibration data.

Inventory succeeds only after every requested read completes and every backup
file is re-read and hashed locally. A successful inventory report is immutable
input to a later install run.

## Install Transaction

Installation requires:

- a previously successful inventory report;
- all backup files and hashes matching that report;
- the live GPT and wrapper boundary evidence matching the inventory;
- a valid Amazon `ABB` BCB;
- one currently selected slot and the other inactive slot;
- the signed OTA passing every release-input check; and
- typed operator confirmation containing the target slot and short image hash.

The transaction is ordered to remain recoverable:

1. Extract the verified `boot.img` to a local temporary file.
2. Select only the inactive redirected `_x` partition.
3. Write exactly 32768 sectors to that partition.
4. Read all 32768 sectors back and verify the complete SHA-256 equals the
   signed release hash.
5. Re-read the 512-byte `misc` sector containing the BCB and require it to equal
   the inventory copy.
6. Preserve every byte in that sector except the seven-byte BCB record.
7. Encode the existing slot as priority 14, zero tries, successful.
8. Encode the target slot as priority 15, three tries, not successful.
9. Write exactly one 512-byte `misc` sector.
10. Read back and verify the complete sector.
11. Save a transaction result before requesting reboot.
12. Reboot and monitor for LibreEcho ADB USB ID `18d1:d001`.

If image writing or verification fails, the BCB is not changed. If the process
stops after the image succeeds but before BCB activation, the new image remains
inactive. No automatic retry may broaden the write set.

## Write Allowlist

An install run may write only:

- all 32768 sectors of one inventory-selected `boot_a_x` or `boot_b_x`; and
- the single 512-byte `misc` sector containing the BCB.

The implementation must have no path that writes GPT, `boot_a`, `boot_b`,
`expdb`, `persist`, `userdata`, `lk_a`, `lk_b`, either TEE partition,
`recovery`, `cache`, RPMB, preloader, TZ, LK, or either eMMC boot area.

## Invalid BCB Handling

Inventory accepts any seven bytes for reporting. Installation requires:

- byte 0 equal to zero;
- bytes 1 through 3 equal to `ABB`;
- version byte equal to one;
- both slot metadata bytes structurally valid; and
- at least one selected bootable slot.

If the live record is malformed, installation stops before any write. Repairing
or migrating `BCb` to `ABB` is a separate design and transaction requiring its
own evidence and approval.

## Recovery Model

The BCB gives the target three attempts and should return to the priority-14
successful slot after exhaustion. However, both redirected slots currently
contain an unproven diagnostic image. The preserved slot is therefore not a
known-good runtime rollback.

BROM and the local backups are the authoritative recovery path. A recovery
operation must be a separate mode that restores only artifacts previously
captured by this tool, verifies their manifest hashes, requires exact live GPT
identity, and performs full readback verification. Recovery implementation is
out of scope for the first boot milestone, but inventory data must be sufficient
to implement it without another successful boot.

## First-Boot Acceptance

The first milestone succeeds only when all of the following are observed:

1. The host sees USB ID `18d1:d001`.
2. `adb devices` lists exactly one LibreEcho device.
3. `adb shell` executes a harmless command successfully.
4. The running `/proc/cmdline`, GPT identities, selected slot, image version,
   and boot image hash match the transaction report.
5. A redacted first-boot report is saved locally.

Wi-Fi, Bluetooth, audio, web setup, feature daemons, and OTA confirmation are
not acceptance requirements for this milestone. The target slot must not be
marked successful merely because ADB enumerates; confirmation requires a later
runtime acceptance decision.

## Deferred Feature Installation

After ADB is stable, a separate transaction will stage each SquashFS and its
manifest under:

```text
/data/libreecho/features/<feature>/payload.squashfs
/data/libreecho/features/<feature>/manifest.json
```

The feature names are `airplay2`, `assistant`, `stt`, `tts`, and `wakeword`.
The four owner-device-local MT8163 firmware files must be extracted from the
owner's untouched stock system partition after exact path, size, and hash
checks. They must not be sourced from or uploaded to CI by this workflow.

## Testing

Offline tests use a fake eMMC device with complete operation logging. They must
prove:

- inventory performs zero writes;
- malformed, duplicated, missing, shifted, or resized GPT entries fail before
  writes;
- disk GUID mismatch fails before writes;
- wrapper evidence mismatch fails before writes;
- backup/report mismatch fails before writes;
- OTA archive hash, member list, signature, manifest, Android magic, size, and
  image hash are independently enforced;
- malformed `BCb` and other invalid BCB values fail before writes;
- only the inactive `_x` slot can be selected;
- exactly 32768 contiguous image-sector writes occur;
- image readback mismatch prevents the BCB write;
- exactly one `misc` sector write occurs after successful image verification;
- all non-BCB bytes in that sector remain unchanged;
- BCB readback mismatch prevents reboot;
- transport failure never causes a write outside the existing allowlist;
- no production function can address forbidden partitions or eMMC areas; and
- reboot occurs only after both complete readback verifications succeed.

The test suite must demonstrate red-green behavior for each safety contract.
Before any live run, shell syntax, Python compilation, unit tests, release
verification, pinned artifact hashes, and the existing Amonet preflight must all
pass freshly on nixtop.

## Operational Stop Conditions

Stop without writing when any of these occurs:

- BROM USB disconnect or re-enumeration;
- unexpected serial response;
- GPT or disk GUID mismatch;
- backup read or local hash failure;
- invalid or ambiguous BCB;
- wrapper evidence mismatch;
- OTA verification failure;
- target slot ambiguity;
- image write/readback mismatch;
- BCB sector changed since inventory; or
- any requested operation falls outside the write allowlist.

The tool must print whether zero writes, image-only writes, or image-plus-BCB
writes occurred before exiting after an error.

## Non-Goals

- Rebuilding LibreEcho with Platform `release/0.12.0` or issue #18 diagnostics.
- Repairing a malformed BCB.
- Installing feature SquashFS payloads.
- Extracting or activating connectivity firmware.
- Configuring Wi-Fi or product services.
- Confirming the new slot as successful.
- Modifying Amonet wrappers or the signed vendor boot chain.

Those are separate follow-up designs after the signed development image reaches
ADB or produces enough evidence to justify a custom build.
