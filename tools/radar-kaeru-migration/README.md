# Radar kaeru-chain migration tool

`migrate.sh` converts a `radar_puffin` that is still running the **old biscuit/k32
boot chain** (the LK used by the pre-radar LibreEcho installs) to the current
**kaeru-based radar amonet chain**, then converts the partition table from the
legacy `_x` layout to the plain layout the kaeru LK expects. The installed
LibreEcho OS image stays exactly where it is.

> **Why the GPT step exists.** The kaeru LK resolves the partition *name*
> `boot_a` and has **no `_x` redirect**. On a unit left on the `_x` layout,
> `fastboot flash boot_a` writes the 110 MiB wrapper (p17), the LK boots p17,
> but bootctl/OTA and the installer use p10/p11 — so the one-shot installer
> fails with `boot_a_x readback hash mismatch` and OTA slot switching would not
> change what boots. Earlier copies of this kit skipped the GPT step.

Typical symptom this fixes: `fastboot getvar product` reports `BISCUIT` on a
radar_puffin, and the one-shot installer warns
`boot-chain identity differs from board: product=BISCUIT explicit-target=radar_puffin`
because the old chain is already unlocked, so the installer never converts it.

## Quickstart (do this in order)

```sh
./migrate.sh -s SERIAL --dry-run     # gates + backups, no writes — always run first
./migrate.sh -s SERIAL               # the conversion (writes + readbacks)
./migrate.sh -s SERIAL --verify      # reboot to fastboot, assert product=RADAR
```

### Already migrated with an older copy of this kit (`boot_a_x readback hash mismatch`)

The chain is already kaeru but the partition table is still `_x`
(`adb shell grep PARTNAME /sys/class/block/mmcblk0p10/uevent` shows `boot_a_x`).
Convert only the GPT, with LibreEcho running and ADB up:

```sh
./migrate.sh -s SERIAL --gpt-fix --dry-run   # validates, saves the current GPT, writes nothing
./migrate.sh -s SERIAL --gpt-fix             # writes backup GPT, then primary, with readbacks
adb -s SERIAL reboot
# then rerun the one-shot installer with --reinstall (erases userdata)
```

`--gpt-fix` refuses on an old-chain unit (the biscuit LK needs `_x`; run the full
migration instead). It is idempotent: on a plain table it does nothing.
Roll back with `./migrate.sh -s SERIAL --gpt-restore` (writes the saved
`backup/gpt-*-orig.bin`, checked against `backup/GPT-MANIFEST.sha256`).
`--no-gpt-fix` runs the old chain-only migration.

If the device is stuck in fastboot with rainbow LEDs (old chain left a
`FASTBOOT_PLEASE` marker in expdb), unstick it first with
`fastboot -s SERIAL erase expdb` + `fastboot -s SERIAL reboot`, get it back into
LibreEcho over ADB, then run the kit. Erasing expdb is safe **only** while the
old chain is in place; once kaeru is installed, expdb holds the kaeru payload
and must never be erased.

## What it checks before writing anything

1. **Chain identity (expdb head).** `88 16 88 58 40` magic → already kaeru, refuses
   (except with `--gpt-fix`, which requires it).
   `FASTBOOT_PLEASE` or all-zero → old chain, proceeds. Anything else → refuses.
2. **LK identity strings.** Warns if `lk_a` does not contain `biscuit` — confirm
   the device really is on the old chain before continuing.
3. **`_x` geometry** against the LibreEcho radar contract
   (`/proc/partitions`, KiB): p3/p5=1024, p4/p6=5120, p7=10240, p10/p11=16384,
   p16=1068544. Mismatch → refuses.

## What it writes (and nothing else)

Sources are the pinned `amonet-radar-v1.0.0` radar package in `stage/`,
SHA-256-pinned by `MANIFEST.sha256` and re-verified on-device after push:

| Source | SHA-256 | Destination |
|---|---|---|
| `lk.bin` | `5070e18839496f18ce8c7e4d3f07e27e3376b96f487ca9c63b081e35e2230db3` | `lk_a` (p3), `lk_b` (p5) |
| `tee-payload.bin` | `91bed859bbcfc3347d9fa5d732332fb1947311dbcb9384b0d7a9c1863af4709f` | `tee1` (p4) |
| `tz.img` | `15cd40caafa6cd8bb8c4e4ed173c973569a8a2b026235d81776ec888f1522c72` | `tee2` (p6) |
| `radar-kaeru.bin` | `02e981b2e055e08e09c71c34f9d88a22d9b1ad316adafb0986e203246566013b` | `expdb` (p7), offset 0 |
| `preloader.img` | `4c7aac4efa7a1bce6922a355c3636e1da575f99bee960d6afd07e1ba107668f` | `mmcblk0boot0` (mknod + force_ro, written LAST) |

Then the GPT step (`gpt-fix.py`, pinned in `MANIFEST.sha256`, stdlib Python 3 on
the host): `boot_a_x`/`boot_b_x` (p10/p11) are renamed `boot_a`/`boot_b` on the
same sectors and the p17/p18 wrapper entries are removed. `userdata` keeps its
start/end (unlike upstream amonet `restore_gpt`, which extends it; the installer
pins its size). The result is byte-identical to the GPT of the hardware-validated
pinned-kaeru dev unit. The tool refuses on any CRC error, primary/backup
disagreement, or geometry that is not the radar `_x` contract.

Never touched: OS image contents, misc, recovery, userdata contents. Every write is followed by a full readback SHA check; the preloader
write order (chain first, preloader last) is copied from the tested
`radar-full-stock-20261009` restore.

## Rollback

`backup/` holds full-partition captures of boot0, lk_a, lk_b, tee1, tee2, expdb
— captured and SHA-verified **before** any write, with `backup/MANIFEST.sha256`.
Rollback is the same dd pattern in reverse (preloader first).

## Support logging — what to share if something goes wrong

Every run writes, under this directory:

- `logs/run-<serial>-<UTC timestamp>.log` — the complete transcript: host
  kernel + fastboot/adb versions, device kernel + uid, serial, every gate
  decision (with the raw expdb head and geometry table when it refuses), all
  backup/staging/write hashes, and a `SUPPORT SUMMARY` line on abort or success.
- `migration.jsonl` — one JSON event per backup/stage/write with SHA-256 and
  UTC timestamp.

If a run aborts or behaves unexpectedly, share **both files** (they contain no
secrets — only serials, hashes and gate output) with LibreEcho support.

## Provenance and status

- Payloads: `amonet-radar-v1.0.0` radar package, pinned by SHA above.
- Write/readback/backup discipline copied from the tested
  `radar-full-stock-20261009/flash.sh` (successful full Radar stock restore log
  ends `ALLDONE`).
- Gates, backups and staging verified live on dev unit `G090P308901401LH`
  (correctly refused: already kaeru). Correction: that dev unit is on the
  **plain** layout (p10/p11 named `boot_a`/`boot_b`, no p17/p18), so it never
  proved the kaeru LK works on `_x`. A field unit (kaeru chain,
  `_x` GPT) showed it does not: one-shot `flash boot_a` booted fine but p10 kept
  the previous image.
- `gpt-fix.py` output on the `_x` form of the dev unit's table is byte-identical
  to the dev unit's real primary and backup GPT.
- Whole kit exercised end to end against a mock adb + disk image (old-chain full
  migration incl. GPT step, `--gpt-fix`, idempotent rerun, `--gpt-restore`,
  refusals). Earlier copies had a staging-path bug (`stage/$img.img`) that made
  every chain write abort before writing; fixed.
- Not yet run on real hardware with the GPT step — first field run is the test;
  dry-run, saved GPT and `--gpt-restore` are the safety net.
